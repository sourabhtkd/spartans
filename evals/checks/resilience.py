"""Resilience and fail-closed behaviour: outages, templates, honest refusals, invalid tests, cache look-alikes."""
from typing import Any

from codeforge.entrypoint import submit
from codeforge.store.db import init_db
from evals.checks.common import FIXED, Env
from tests.helpers import (
    memory_graph,
    outage,
    patch_llm,
    sandbox_spy,
    scripted_fake,
)


def check_outage_template(case: dict, env: Env) -> None:
    outage(env.mp)
    res = submit(case["input"], graph=memory_graph())
    assert res.values["served_by"] == "template"
    assert res.values["run_result"].exit_code == 0

def check_outage_unknown(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    outage(env.mp)
    res = submit(case["input"], graph=memory_graph())
    assert res.values["served_by"] == "refused" and res.values["ticket"] is not None
    assert "cannot generate code right now" in res.values["report"]
    assert calls == []

def check_refused_never_executes(case: dict, env: Env) -> None:
    check_outage_unknown(case, env)

def check_degraded_no_auto_approve(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    outage(env.mp)
    res = submit(case["input"], graph=memory_graph())
    assert calls == [] and res.values.get("approval") is None and not res.pending_approval

def check_invalid_tests_never_run(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    weak = "from solution import f\ndef test_a():\n    assert f(1) == 2\n"
    patch_llm(env.mp, scripted_fake(FIXED, weak))
    res = submit(case["input"], graph=memory_graph())
    assert calls == [] and res.values["ticket"] is not None

def check_cache_lookalike(case: dict, env: Env) -> None:
    from codeforge.cost.semantic_cache import extract_guard_tokens

    assert extract_guard_tokens("sort list ascending") != extract_guard_tokens("sort list descending")

def check_cache_lookalike_store(case: dict, env: Env) -> None:
    from codeforge.cost.semantic_cache import SemanticCache

    cache = SemanticCache(session_factory=init_db("sqlite:///:memory:"))
    a, b = case["input"].split("|")
    cache.store(a, "code", "tests")
    assert cache.lookup(b) is None
    assert cache.lookup(a) is not None


CHECKS: dict[str, Any] = {
    "outage_template": check_outage_template,
    "outage_unknown": check_outage_unknown,
    "refused_never_executes": check_refused_never_executes,
    "degraded_no_auto_approve": check_degraded_no_auto_approve,
    "invalid_tests_never_run": check_invalid_tests_never_run,
    "cache_lookalike": check_cache_lookalike,
    "cache_lookalike_store": check_cache_lookalike_store,
}
