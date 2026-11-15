import os
import sqlite3
from typing import Any, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from codeforge.agents.codegen import CodeGenAgent
from codeforge.agents.requirements import RequirementsAgent
from codeforge.agents.router import RouterAgent
from codeforge.agents.test_validator import TestValidator
from codeforge.agents.testgen import TestGenAgent
from codeforge.config import current_identity, get_settings
from codeforge.guard.pii import mask_text
from codeforge.hitl.approval import human_approval_node
from codeforge.hitl.handoff import create_ticket, should_escalate
from codeforge.observability import get_logger
from codeforge.policy.code_policy import evaluate_code_policy

logger = get_logger("graph")
from codeforge.report import render_report
from codeforge.sandbox.runner import run_pytest_sandboxed
from codeforge.state import CodeForgeState, PolicyDecision, RunResult
from codeforge.store.runs import (
    QuotaExceeded,
    RunStore,
    compute_idempotency_key,
)


def ingress_guard(state: CodeForgeState) -> dict[str, Any]:
    raw_req = state.get("raw_request_masked", "")
    masked_req = mask_text(raw_req)
    # Identity is ALWAYS read from config, never from graph input.
    identity = current_identity()

    store = RunStore(identity_provider=lambda: identity)
    try:
        store.check_and_increment_quota()
    except QuotaExceeded as e:
        return {
            "raw_request_masked": masked_req,
            "identity": identity,
            "report": f"❌ Quota Exceeded: {e}",
            "events": [{"stage": "quota_check", "status": "exceeded"}],
        }

    # Pre-check explicit human request escalation
    should_esc, reason, prio = should_escalate(masked_req)
    ticket = None
    if should_esc:
        ticket = create_ticket(
            {"raw_request_masked": masked_req, "identity": identity},
            priority=prio,
            reason=reason,
        )

    return {
        "raw_request_masked": masked_req,
        "identity": identity,
        "ticket": ticket,
        "attempts": state.get("attempts", 0),
        "events": list(state.get("events", [])),
    }


def cache_lookup(state: CodeForgeState) -> dict[str, Any]:
    # Hook for guarded semantic cache (Task 14)
    # Checks whether semantic cache has a verified solution
    try:
        from codeforge.cost.semantic_cache import get_semantic_cache

        cache = get_semantic_cache()
        entry = cache.lookup(state.get("raw_request_masked", ""))
        if entry:
            return {
                "code": entry["code"],
                "tests": entry["tests"],
                "cache_hit": True,
                "served_tier": "cache",
            }
    except Exception as e:  # noqa: BLE001
        logger.debug("cache_lookup_miss_or_error", error=str(e))
    return {"cache_hit": False}


def router_node(state: CodeForgeState) -> dict[str, Any]:
    agent = RouterAgent()
    return agent(state)


def requirements_node(state: CodeForgeState) -> dict[str, Any]:
    agent = RequirementsAgent()
    return agent(state)


def codegen_node(state: CodeForgeState) -> dict[str, Any]:
    agent = CodeGenAgent()
    return agent(state)


def testgen_node(state: CodeForgeState) -> dict[str, Any]:
    agent = TestGenAgent()
    return agent(state)


def test_validator_node(state: CodeForgeState) -> dict[str, Any]:
    validator = TestValidator()
    return validator(state)


def policy_gate_node(state: CodeForgeState) -> dict[str, Any]:
    code = state.get("code", "")
    tests = state.get("tests", "")
    decision = evaluate_code_policy(code + "\n" + tests)
    return {"policy": decision}


def refuse_node(state: CodeForgeState) -> dict[str, Any]:
    policy = state.get("policy")
    if policy is None:
        # Fail closed: no policy decision means nothing may run.
        policy = PolicyDecision(
            verdict="deny", reasons=["No policy decision available (fail closed)"]
        )
    events = list(state.get("events", []))
    events.append({"stage": "refuse", "reasons": policy.reasons})
    return {"events": events, "policy": policy}


def sandbox_run_node(state: CodeForgeState) -> dict[str, Any]:
    identity = current_identity()
    code = state.get("code", "")
    tests = state.get("tests", "")
    idem_key = compute_idempotency_key(identity.user_id, code, tests)
    timeout_s = get_settings().sandbox.get("timeout_s", 10)

    store = RunStore(identity_provider=lambda: identity)
    cached_result = store.get_by_idem_key(idem_key)
    if cached_result:
        return {"run_result": cached_result, "idempotency_key": idem_key}

    thread_id = state.get("thread_id", "run")
    run_id = f"{thread_id}_{idem_key[:8]}"
    if not store.claim(run_id, idem_key, state.get("raw_request_masked", ""), code, tests):
        # Another worker owns this exact run: wait for its result, never execute twice.
        waited = store.wait_for_result(idem_key, max_wait_s=timeout_s + 5)
        if waited is None:
            waited = RunResult(
                exit_code=None,
                stdout="An identical run is still in progress; please retry shortly.",
            )
        return {"run_result": waited, "idempotency_key": idem_key}

    result = run_pytest_sandboxed(code, tests, timeout_s=timeout_s)
    store.complete(idem_key, result)
    return {"run_result": result, "idempotency_key": idem_key}


def cache_store_node(state: CodeForgeState) -> dict[str, Any]:
    # Hook for guarded semantic cache (Task 14)
    run_res = state.get("run_result")
    if run_res and run_res.exit_code == 0:
        try:
            from codeforge.cost.semantic_cache import get_semantic_cache

            cache = get_semantic_cache()
            cache.store(
                request_text=state.get("raw_request_masked", ""),
                code=state.get("code", ""),
                tests=state.get("tests", ""),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("cache_store_error", error=str(e))
    return {}


def escalate_check_node(state: CodeForgeState) -> dict[str, Any]:
    if state.get("ticket"):
        return {}

    if state.get("served_tier") == "refused":
        ticket = create_ticket(
            state, priority="P2", reason="CodeForge cannot generate code right now (model outage, no template)"
        )
        return {"ticket": ticket}
    if state.get("escalate_reason"):
        ticket = create_ticket(state, priority="P2", reason=state["escalate_reason"])
        return {"ticket": ticket}

    req_text = state.get("raw_request_masked", "")
    policy = state.get("policy")
    policy_denials = 0
    if policy and policy.verdict == "deny":
        # Session-level counter: repeated denials across requests escalate (R3 rule).
        identity = current_identity()
        policy_denials = RunStore(identity_provider=lambda: identity).record_policy_denial()
    attempts = state.get("attempts", 0)

    should_esc, reason, prio = should_escalate(
        req_text, policy_denials=policy_denials, repair_attempts=attempts
    )
    if should_esc:
        ticket = create_ticket(state, priority=prio, reason=reason)
        return {"ticket": ticket}
    return {}


def report_node(state: CodeForgeState) -> dict[str, Any]:
    # If report was already set by quota exceeded, keep it
    if state.get("report") and "Quota Exceeded" in state["report"]:
        return {}
    rendered = render_report(state)
    return {"report": rendered}


# Conditional routing predicates
def route_after_ingress(state: CodeForgeState) -> Literal["report", "cache_lookup"]:
    if state.get("report") and "Quota Exceeded" in state["report"]:
        return "report"
    ticket = state.get("ticket")
    if ticket and ticket.priority == "P1":
        return "report"  # urgent: hand off immediately, short reply
    return "cache_lookup"  # lower-priority human requests still get the pipeline


def route_after_cache(state: CodeForgeState) -> Literal["test_validator", "router"]:
    # Cache hits still pass the test validator, policy gate and sandbox.
    if state.get("cache_hit"):
        return "test_validator"
    return "router"


def route_after_codegen(
    state: CodeForgeState,
) -> Literal["testgen", "policy_gate", "escalate_check"]:
    if not state.get("code", "").strip() or state.get("served_tier") == "refused":
        return "escalate_check"  # nothing to run: never sandbox empty code
    if state.get("tests_frozen"):
        return "policy_gate"  # repair loop: tests are frozen, only code changes
    return "testgen"


def route_after_testgen(state: CodeForgeState) -> Literal["test_validator", "escalate_check"]:
    if not state.get("tests", "").strip():
        return "escalate_check"
    return "test_validator"


def route_after_validator(
    state: CodeForgeState,
) -> Literal["policy_gate", "testgen", "escalate_check"]:
    if state.get("tests_valid", True):
        return "policy_gate"
    if state.get("test_regen_count", 0) < 2:
        return "testgen"  # regenerate invalid tests once
    return "escalate_check"


def route_after_router(state: CodeForgeState) -> Literal["requirements", "report"]:
    route = state.get("route")
    if route and route.kind == "python_function":
        return "requirements"
    return "report"


def route_after_policy(
    state: CodeForgeState,
) -> Literal["refuse", "human_approval", "sandbox_run"]:
    policy = state.get("policy")
    if policy is None:
        return "refuse"  # fail closed
    if policy.verdict == "deny":
        return "refuse"
    if policy.verdict == "needs_approval":
        return "human_approval"
    return "sandbox_run"


def route_after_approval(
    state: CodeForgeState,
) -> Literal["sandbox_run", "escalate_check"]:
    approval = state.get("approval")
    if approval and approval.approved:
        return "sandbox_run"
    return "escalate_check"


def route_after_sandbox(
    state: CodeForgeState,
) -> Literal["cache_store", "codegen", "escalate_check"]:
    res = state.get("run_result")
    attempts = state.get("attempts", 0)
    if res and res.exit_code == 0 and not res.timed_out:
        return "cache_store"
    if attempts < 2:
        return "codegen"
    return "escalate_check"


_CHECKPOINTERS: dict[str, SqliteSaver] = {}


def _shared_checkpointer() -> SqliteSaver:
    """One SqliteSaver per checkpoint path (config storage.checkpoint_path)."""
    path = os.environ.get("CODEFORGE_CHECKPOINT_PATH") or str(
        get_settings().storage.get("checkpoint_path", "checkpoints.db")
    )
    if path not in _CHECKPOINTERS:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        _CHECKPOINTERS[path] = SqliteSaver(conn)
    return _CHECKPOINTERS[path]


def build_graph(checkpointer: Any | None = None) -> Any:
    """Builds and compiles the complete CodeForge StateGraph."""
    graph = StateGraph(CodeForgeState)

    # Add all nodes
    graph.add_node("ingress_guard", ingress_guard)
    graph.add_node("cache_lookup", cache_lookup)
    graph.add_node("router", router_node)
    graph.add_node("requirements", requirements_node)
    graph.add_node("codegen", codegen_node)
    graph.add_node("testgen", testgen_node)
    graph.add_node("test_validator", test_validator_node)
    graph.add_node("policy_gate", policy_gate_node)
    graph.add_node("refuse", refuse_node)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("sandbox_run", sandbox_run_node)
    graph.add_node("cache_store", cache_store_node)
    graph.add_node("escalate_check", escalate_check_node)
    graph.add_node("report", report_node)

    # Connect edges
    graph.add_edge(START, "ingress_guard")
    graph.add_conditional_edges("ingress_guard", route_after_ingress)
    graph.add_conditional_edges("cache_lookup", route_after_cache)
    graph.add_conditional_edges("router", route_after_router)
    graph.add_edge("requirements", "codegen")
    graph.add_conditional_edges("codegen", route_after_codegen)
    graph.add_conditional_edges("testgen", route_after_testgen)
    graph.add_conditional_edges("test_validator", route_after_validator)
    graph.add_conditional_edges("policy_gate", route_after_policy)
    graph.add_edge("refuse", "escalate_check")
    graph.add_conditional_edges("human_approval", route_after_approval)
    graph.add_conditional_edges("sandbox_run", route_after_sandbox)
    graph.add_edge("cache_store", "escalate_check")
    graph.add_edge("escalate_check", "report")
    graph.add_edge("report", END)

    if checkpointer is None:
        checkpointer = _shared_checkpointer()

    return graph.compile(checkpointer=checkpointer)
