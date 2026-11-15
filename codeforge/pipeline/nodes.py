"""The graph's nodes. Each takes the pipeline state and returns the fields it wants to change.

Order of the pipeline (see builder.py for the wiring and routing.py for the decisions):
ingress_guard -> cache_lookup -> router -> requirements -> codegen -> testgen -> test_validator
-> policy_gate -> (refuse | human_approval | sandbox_run) -> evaluator -> cache_store
-> escalate_check -> report
"""
from typing import Any

from codeforge.agents.codegen import CodeGenAgent
from codeforge.agents.evaluator import EvaluatorAgent
from codeforge.agents.history import compact_history, llm_summarize
from codeforge.agents.requirements import RequirementsAgent
from codeforge.agents.router import RouterAgent
from codeforge.agents.test_validator import TestValidator
from codeforge.agents.testgen import TestGenAgent
from codeforge.config import current_identity, get_settings
from codeforge.cost.semantic_cache import get_semantic_cache
from codeforge.hitl.approval import human_approval_node  # noqa: F401 - re-exported for builder.py
from codeforge.hitl.handoff import create_ticket, should_escalate
from codeforge.observability import get_logger
from codeforge.policy.code_policy import evaluate_code_policy
from codeforge.privacy.pii import mask_text
from codeforge.report import render_report
from codeforge.sandbox.runner import run_pytest_sandboxed
from codeforge.state import CodeForgeState, PolicyDecision, RunResult
from codeforge.store.runs import QuotaExceeded, RunStore, compute_idempotency_key

logger = get_logger("nodes")


def _compacted_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shorten long (already masked) chat history before any agent sees it."""
    cfg = get_settings().compaction
    max_turns = int(cfg.get("max_turns", 6))
    if len(history) <= max_turns:
        return history
    return compact_history(history, max_turns, int(cfg.get("keep_last", 4)), summarizer=llm_summarize)


def ingress_guard(state: CodeForgeState) -> dict[str, Any]:
    raw_req = state.get("request_masked", "")
    masked_req = mask_text(raw_req)
    # Identity is ALWAYS read from config, never from graph input.
    identity = current_identity()

    store = RunStore(identity_provider=lambda: identity)
    try:
        store.check_and_increment_quota()
    except QuotaExceeded as e:
        return {
            "request_masked": masked_req,
            "identity": identity,
            "quota_exceeded": True,
            "report": f"❌ Quota Exceeded: {e}",
            "events": [{"stage": "quota_check", "status": "exceeded"}],
        }

    history = _compacted_history(state.get("history") or [])

    # An explicit request for a human opens a ticket right away.
    should_esc, reason, prio = should_escalate(masked_req)
    ticket = None
    if should_esc:
        ticket = create_ticket(
            {"request_masked": masked_req, "identity": identity},
            priority=prio,
            reason=reason,
        )

    return {
        "request_masked": masked_req,
        "history": history,
        "identity": identity,
        "ticket": ticket,
        "attempts": state.get("attempts", 0),
        "events": list(state.get("events", [])),
    }


def cache_lookup(state: CodeForgeState) -> dict[str, Any]:
    """Reuse a verified solution from the guarded semantic cache (a hit still goes through every gate)."""
    try:
        cache = get_semantic_cache()
        entry = cache.lookup(
            state.get("request_masked", ""),
            context_free=not state.get("history"),
            role=current_identity().role,
        )
        if entry:
            return {
                "code": entry["code"],
                "tests": entry["tests"],
                "cache_hit": True,
                "served_by": "cache",
            }
    except Exception as e:  # noqa: BLE001
        logger.debug("cache_lookup_miss_or_error", error=str(e))
    return {"cache_hit": False}


def router_node(state: CodeForgeState) -> dict[str, Any]:
    return RouterAgent()(state)


def requirements_node(state: CodeForgeState) -> dict[str, Any]:
    return RequirementsAgent()(state)


def codegen_node(state: CodeForgeState) -> dict[str, Any]:
    return CodeGenAgent()(state)


def testgen_node(state: CodeForgeState) -> dict[str, Any]:
    return TestGenAgent()(state)


def test_validator_node(state: CodeForgeState) -> dict[str, Any]:
    return TestValidator()(state)


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
    if not store.claim_execution(run_id, idem_key, state.get("request_masked", ""), code, tests):
        # Another worker owns this exact run: wait for its result, never execute twice.
        waited = store.wait_for_result(idem_key, max_wait_s=timeout_s + 5)
        if waited is None:
            waited = RunResult(
                exit_code=None,
                stdout="An identical run is still in progress; please retry shortly.",
            )
        return {"run_result": waited, "idempotency_key": idem_key}

    result = run_pytest_sandboxed(code, tests, timeout_s=timeout_s)
    store.finish_execution(idem_key, result)
    return {"run_result": result, "idempotency_key": idem_key}


def cache_store_node(state: CodeForgeState) -> dict[str, Any]:
    """After a passing run, remember the solution in the semantic cache (context-free requests only)."""
    run_res = state.get("run_result")
    passed = bool(run_res and run_res.exit_code == 0)
    if passed and not state.get("history"):
        try:
            get_semantic_cache().store(
                request_text=state.get("request_masked", ""),
                code=state.get("code", ""),
                tests=state.get("tests", ""),
                role=current_identity().role,
            )
        except Exception as e:  # noqa: BLE001 - caching is best-effort
            logger.debug("cache_store_error", error=str(e))
    return {}


def evaluator_node(state: CodeForgeState) -> dict[str, Any]:
    """Advisory review after a passing run; never affects pass/fail."""
    return EvaluatorAgent()(state)


def escalate_check_node(state: CodeForgeState) -> dict[str, Any]:
    if state.get("ticket"):
        return {}

    if state.get("served_by") == "refused":
        ticket = create_ticket(
            state, priority="P2", reason="CodeForge cannot generate code right now (model outage, no template)"
        )
        return {"ticket": ticket}
    if state.get("escalate_reason"):
        ticket = create_ticket(state, priority="P2", reason=state["escalate_reason"])
        return {"ticket": ticket}

    req_text = state.get("request_masked", "")
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
    if state.get("quota_exceeded"):  # ingress already wrote the report
        return {}
    rendered = render_report(state)
    return {"report": rendered}
