"""Routing decisions: which node runs next. Pure functions of the state, no side effects.

Every function returns the NAME of the next node; builder.py connects them to the graph.
"""
from typing import Literal

from codeforge.state import CodeForgeState

MAX_REPAIR_ATTEMPTS = 2  # code is regenerated at most this many times after failing tests
MAX_TEST_REGENERATIONS = 2  # invalid generated tests are regenerated once (count reaches 2 = give up)


def route_after_ingress(state: CodeForgeState) -> Literal["report", "cache_lookup"]:
    if state.get("quota_exceeded"):
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
    if not state.get("code", "").strip() or state.get("served_by") == "refused":
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
    if state.get("test_regen_count", 0) < MAX_TEST_REGENERATIONS:
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
) -> Literal["evaluator", "cache_store", "codegen", "escalate_check"]:
    res = state.get("run_result")
    attempts = state.get("attempts", 0)
    if res and res.exit_code == 0 and not res.timed_out:
        # Review only fresh model output: cache hits and templates have nothing new to critique.
        if state.get("served_by") in ("primary", "fallback") and not state.get("cache_hit"):
            return "evaluator"
        return "cache_store"
    if attempts < MAX_REPAIR_ATTEMPTS:
        return "codegen"
    return "escalate_check"
