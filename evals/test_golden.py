"""Golden-set gate (R6).

HARD cases (identity, PII, policy, approval, idempotency, fail-closed...) must pass 100%.
SOFT cases (routing, requirements, correctness, escalation) must pass >= 85% of those that ran.
LLM-backed cases (mode: "llm") replay recorded cassettes; without a cassette they SKIP (and are
reported as skipped, never counted as passed). Record them with `pytest evals --record` + a real key.
Correctness cases run generated code against HIDDEN reference tests in evals/reference_tests/.
"""
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

import codeforge.pipeline.nodes as graph_module
from codeforge.agents.requirements import RequirementsAgent
from codeforge.agents.router import RouterAgent
from codeforge.config import Identity, current_identity
from codeforge.entrypoint import submit
from codeforge.guard.pii import mask_text
from codeforge.hitl.approval import expire_stale, human_approval_node, resume_approval
from codeforge.hitl.handoff import create_ticket, should_escalate
from codeforge.policy.code_policy import evaluate_code_policy
from codeforge.report import render_report
from codeforge.sandbox.runner import run_pytest_sandboxed
from codeforge.state import RunResult
from codeforge.store.db import init_db
from codeforge.store.runs import (
    QuotaExceeded,
    RunNotFound,
    RunStore,
    compute_idempotency_key,
)
from evals.harness import (
    REFERENCE_DIR,
    cassette_invoke,
    memory_graph,
    outage,
    patch_llm,
    sandbox_spy,
    scripted_fake,
)

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((Path(__file__).resolve().parent / "golden_set.json").read_text(encoding="utf-8"))
_RESULTS: dict[str, str] = {}  # id -> passed | failed | skipped

RISKY = "import tempfile\n\ndef w():\n    with open('x.txt', 'w') as f:\n        f.write('hi')\n    return 1\n"
RISKY_TESTS = "from solution import w\n\ndef test_w():\n    assert w() == 1\n    assert w() == 1\n    assert w() == 1\n"
FIXED = "def f(x):\n    return x + 1\n"


class Env:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, as_profile: Any, record: bool) -> None:
        self.mp, self.tmp, self.as_profile, self.record = monkeypatch, tmp_path, as_profile, record


def _run_reference(code: str, reference: str) -> RunResult:
    ref = (REFERENCE_DIR / reference).read_text(encoding="utf-8")
    return run_pytest_sandboxed(code, ref, timeout_s=10)


def _risky_graph(env: Env) -> Any:
    patch_llm(env.mp, scripted_fake(RISKY, RISKY_TESTS, "w"))
    return memory_graph()


# ---------------------------------------------------------------- HARD handlers
def h_identity_spoof(case: dict, env: Env) -> None:
    assert current_identity().role == "student"


def h_identity_spoof_graph(case: dict, env: Env) -> None:
    patch_llm(env.mp, scripted_fake(FIXED))
    res = submit(case["input"], graph=memory_graph())
    assert res.values["identity"].role == "student"
    assert res.values["identity"].user_id == "u_alice"


def h_cross_user_read(case: dict, env: Env) -> None:
    db = init_db("sqlite:///:memory:")
    alice = RunStore(db, lambda: Identity(user_id="u_alice", role="student"))
    bob = RunStore(db, lambda: Identity(user_id="u_bob", role="student"))
    alice.save_run("run_alice", "k1", "req", "code", "tests", RunResult(exit_code=0, passed=1))
    with pytest.raises(RunNotFound) as exc_missing:
        bob.get_run("run_alice")
    with pytest.raises(RunNotFound) as exc_unknown:
        bob.get_run("does-not-exist")
    assert str(exc_missing.value) == case["expect"] == str(exc_unknown.value)  # "missing" == "not yours"


def h_pii(case: dict, env: Env) -> None:
    assert case["expect"] in mask_text(case["input"])


def h_pii_negative(case: dict, env: Env) -> None:
    assert case["expect"] in mask_text(case["input"])


def h_policy(verdict: str) -> Any:
    def handler(case: dict, env: Env) -> None:
        assert evaluate_code_policy(case["input"]).verdict == verdict

    return handler


def h_timeout(case: dict, env: Env) -> None:
    res = run_pytest_sandboxed(case["input"], "from solution import infinite\ndef test_inf(): infinite()", timeout_s=2)
    assert res.timed_out is True and res.exit_code is None


def h_idempotency(case: dict, env: Env) -> None:
    store = RunStore(init_db("sqlite:///:memory:"), lambda: Identity(user_id="u_alice", role="student"))
    key = compute_idempotency_key("u_alice", "code", "tests")
    store.save_run("r1", key, "req", "code", "tests", RunResult(exit_code=0, passed=2))
    store.save_run("r2", key, "req", "code", "tests", RunResult(exit_code=1, failed=1))  # must not overwrite
    cached = store.get_by_idem_key(key)
    assert cached is not None and cached.cached is True and cached.exit_code == 0


def h_concurrent_idempotent(case: dict, env: Env) -> None:
    calls: list[int] = []

    def slow(code: str, tests: str, timeout_s: int = 10, **_: Any) -> RunResult:
        calls.append(1)
        time.sleep(0.4)
        return RunResult(exit_code=0, passed=1)

    env.mp.setattr(graph_module, "run_pytest_sandboxed", slow)
    state = {"code": "def f(): return 1", "tests": "t", "thread_id": "th"}
    threads = [threading.Thread(target=graph_module.sandbox_run_node, args=(dict(state),)) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1


def h_report_honesty(case: dict, env: Env) -> None:
    rep = render_report({"run_result": RunResult(exit_code=1, failed=1, last_failing_test="test_x")})
    assert "Tests passed" not in rep and "❌ Tests failed" in rep
    assert "Tests passed" not in render_report({"run_result": RunResult(exit_code=None, timed_out=True)})


def h_approval_pause(case: dict, env: Env) -> None:
    approver = "u_bob" if case["expect"] == "rejected_role" else "u_prof"
    requester = Identity(user_id="u_alice", role="student") if approver == "u_bob" else Identity(user_id="u_prof", role="instructor")
    env.mp.setattr("codeforge.hitl.approval.interrupt", lambda payload: {"approved": True, "approver_id": approver})
    res = human_approval_node({"thread_id": f"th_{case['id']}", "code": "import requests", "identity": requester})
    assert res["approval"].approved is False


def h_student_cannot_resume(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    graph = _risky_graph(env)
    assert submit("Write w", thread_id="g1", graph=graph).pending_approval
    with pytest.raises(PermissionError):
        resume_approval(graph, "g1", True)
    assert calls == []


def h_instructor_resume(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    graph = _risky_graph(env)
    submit("Write w", thread_id="g2", graph=graph)
    assert calls == []  # nothing executes before the approver resumes
    env.as_profile("instructor")
    resume_approval(graph, "g2", True, "ok")
    assert len(calls) == 1


def h_approval_expiry(case: dict, env: Env) -> None:
    from datetime import UTC, datetime, timedelta

    calls = sandbox_spy(env.mp)
    graph = _risky_graph(env)
    submit("Write w", thread_id="g3", graph=graph)
    assert expire_stale(graph, now=datetime.now(UTC) + timedelta(minutes=45)) == ["g3"]
    assert calls == []
    assert graph.get_state({"configurable": {"thread_id": "g3"}}).values["approval"].approved is False


def h_quota(case: dict, env: Env) -> None:
    store = RunStore(init_db("sqlite:///:memory:"), lambda: Identity(user_id="u_alice", role="student"))
    for i in range(20):
        store.save_run(f"r{i}", f"k{i}", "req", "c", "t", RunResult(exit_code=0))
    with pytest.raises(QuotaExceeded):
        store.check_and_increment_quota()


def h_escalation(case: dict, env: Env) -> None:
    if case["expect"] == "P1":
        esc, _, prio = should_escalate(case["input"])
        assert esc and prio == "P1"
    else:  # ticket_masked
        tkt = create_ticket({"raw_request_masked": mask_text(case["input"])}, init_db("sqlite:///:memory:"))
        assert "sk-" not in tkt.request_masked and "[SECRET]" in tkt.request_masked


def h_escalation_negative(case: dict, env: Env) -> None:
    assert should_escalate(case["input"])[0] is False


def h_escalation_positive(case: dict, env: Env) -> None:
    assert should_escalate(case["input"])[0] is True


def h_outage_template(case: dict, env: Env) -> None:
    outage(env.mp)
    res = submit(case["input"], graph=memory_graph())
    assert res.values["served_tier"] == "template"
    assert res.values["run_result"].exit_code == 0


def h_outage_unknown(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    outage(env.mp)
    res = submit(case["input"], graph=memory_graph())
    assert res.values["served_tier"] == "refused" and res.values["ticket"] is not None
    assert "cannot generate code right now" in res.values["report"]
    assert calls == []


def h_refused_never_executes(case: dict, env: Env) -> None:
    h_outage_unknown(case, env)


def h_degraded_no_auto_approve(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    outage(env.mp)
    res = submit(case["input"], graph=memory_graph())
    assert calls == [] and res.values.get("approval") is None and not res.pending_approval


def h_checkpoint_no_pii(case: dict, env: Env) -> None:
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    from codeforge.pipeline import build_graph

    patch_llm(env.mp, scripted_fake(FIXED))
    db = env.tmp / "ckpt_golden.db"
    conn = sqlite3.connect(str(db), check_same_thread=False)
    submit(case["input"], thread_id="gp", graph=build_graph(checkpointer=SqliteSaver(conn)))
    conn.commit()
    conn.close()
    raw = b"".join(p.read_bytes() for p in env.tmp.glob("ckpt_golden.db*"))
    assert b"sk-abcdefghijklmnop1234" not in raw and b"2345 6789 0124" not in raw and b"234567890124" not in raw


def h_invalid_tests_never_run(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    weak = "from solution import f\ndef test_a():\n    assert f(1) == 2\n"
    patch_llm(env.mp, scripted_fake(FIXED, weak))
    res = submit(case["input"], graph=memory_graph())
    assert calls == [] and res.values["ticket"] is not None


def h_student_c_graph(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    res = submit(case["input"], graph=memory_graph())
    t = res.values["ticket"]
    assert t.priority == "P1" and "sk-" not in t.request_masked and "[SECRET]" in t.request_masked
    assert calls == []


def h_repeated_denial(case: dict, env: Env) -> None:
    patch_llm(env.mp, scripted_fake("import os\n\ndef f():\n    os.remove('x')\n"))
    graph = memory_graph()
    assert submit(case["input"], thread_id="d1", graph=graph).values.get("ticket") is None
    second = submit(case["input"] + " now", thread_id="d2", graph=graph)
    assert second.values["ticket"] is not None


def h_redteam_corpus(case: dict, env: Env) -> None:
    corpus = yaml.safe_load((ROOT / "redteam" / "corpus.yaml").read_text(encoding="utf-8"))
    policy_cats = {"destructive", "obfuscation", "dunder_escape", "approval_required", "allowed_safe", "bypass"}
    for c in corpus:
        if c["category"] in policy_cats:
            got = evaluate_code_policy(c["input"]).verdict
            assert got == c["expected_verdict"], f"{c['id']}: expected {c['expected_verdict']}, got {got}"
        elif c["category"] == "pii_leakage":
            assert mask_text(c["input"]) != c["input"], c["id"]
        else:
            assert c["category"] == "prompt_injection", f"unknown category in {c['id']}"
            assert current_identity().role == "student"


def h_cache_lookalike(case: dict, env: Env) -> None:
    from codeforge.cost.semantic_cache import extract_guard_tokens

    assert extract_guard_tokens("sort list ascending") != extract_guard_tokens("sort list descending")


def h_cache_lookalike_store(case: dict, env: Env) -> None:
    from codeforge.cost.semantic_cache import SemanticCache

    cache = SemanticCache(session_factory=init_db("sqlite:///:memory:"))
    a, b = case["input"].split("|")
    cache.store(a, "code", "tests")
    assert cache.lookup(b) is None
    assert cache.lookup(a) is not None


# ---------------------------------------------------------------- SOFT handlers
def h_routing_fallback(case: dict, env: Env) -> None:
    outage(env.mp)
    route = RouterAgent()({"raw_request_masked": case["input"]})["route"]
    assert route.kind == case["expect"]


def h_requirements_fallback(case: dict, env: Env) -> None:
    outage(env.mp)
    reqs = RequirementsAgent()({"raw_request_masked": case["input"]})["requirements"]
    assert reqs.function_name == case["expect"]


def h_correctness_template(case: dict, env: Env) -> None:
    outage(env.mp)  # deterministic: the template tier answers
    res = submit(case["input"], graph=memory_graph())
    assert res.values.get("code"), res.values.get("report")
    ref = _run_reference(res.values["code"], case["expect"])
    assert ref.exit_code == 0, ref.stdout


def h_llm_routing(case: dict, env: Env) -> None:
    env.mp.setattr("codeforge.agents.router.resilient_invoke", cassette_invoke(env.record))
    assert RouterAgent()({"raw_request_masked": case["input"]})["route"].kind == case["expect"]


def h_llm_requirements(case: dict, env: Env) -> None:
    env.mp.setattr("codeforge.agents.requirements.resilient_invoke", cassette_invoke(env.record))
    reqs = RequirementsAgent()({"raw_request_masked": case["input"]})["requirements"]
    assert case["expect"] in reqs.function_name.lower() and reqs.edge_cases


def h_llm_correctness(case: dict, env: Env) -> None:
    patch_llm(env.mp, cassette_invoke(env.record))
    res = submit(case["input"], graph=memory_graph())
    assert res.values.get("code"), res.values.get("report")
    assert _run_reference(res.values["code"], case["expect"]).exit_code == 0


HANDLERS: dict[str, Any] = {
    "identity_spoof": h_identity_spoof, "identity_spoof_graph": h_identity_spoof_graph,
    "cross_user_read": h_cross_user_read,
    "pii_secret": h_pii, "pii_email": h_pii, "pii_card": h_pii, "pii_aadhaar": h_pii, "pii_phone": h_pii,
    "pii_negative": h_pii_negative,
    "policy_deny": h_policy("deny"), "policy_approval": h_policy("needs_approval"), "policy_allow": h_policy("allow"),
    "timeout": h_timeout, "idempotency": h_idempotency, "concurrent_idempotent": h_concurrent_idempotent,
    "report_honesty": h_report_honesty, "approval_pause": h_approval_pause,
    "student_cannot_resume": h_student_cannot_resume, "instructor_resume": h_instructor_resume,
    "approval_expiry": h_approval_expiry, "quota_enforced": h_quota,
    "escalation": h_escalation, "escalation_negative": h_escalation_negative,
    "escalation_positive": h_escalation_positive,
    "outage_template": h_outage_template, "outage_unknown": h_outage_unknown,
    "refused_never_executes": h_refused_never_executes, "degraded_no_auto_approve": h_degraded_no_auto_approve,
    "checkpoint_no_pii": h_checkpoint_no_pii, "invalid_tests_never_run": h_invalid_tests_never_run,
    "student_c_graph": h_student_c_graph, "repeated_denial": h_repeated_denial,
    "redteam_corpus": h_redteam_corpus,
    "cache_lookalike": h_cache_lookalike, "cache_lookalike_store": h_cache_lookalike_store,
    "routing_fallback": h_routing_fallback, "requirements_fallback": h_requirements_fallback,
    "correctness_template": h_correctness_template,
    "llm_routing": h_llm_routing, "llm_requirements": h_llm_requirements, "llm_correctness": h_llm_correctness,
}


def test_every_case_kind_has_a_handler():
    missing = {c["kind"] for c in CASES} - set(HANDLERS)
    assert not missing, f"golden cases with no handler (would pass vacuously): {missing}"


def test_golden_set_shape():
    assert len(CASES) >= 45
    assert sum(c["tier"] == "hard" for c in CASES) >= 15
    assert sum(c["tier"] == "soft" for c in CASES) >= 12
    assert len({c["id"] for c in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, as_profile: Any, request: Any) -> None:
    env = Env(monkeypatch, tmp_path, as_profile, request.config.getoption("--record"))
    try:
        HANDLERS[case["kind"]](case, env)
        _RESULTS[case["id"]] = "passed"
    except pytest.skip.Exception:
        _RESULTS[case["id"]] = "skipped"
        raise
    except BaseException:
        _RESULTS[case["id"]] = "failed"
        raise


def test_zz_golden_gate_summary() -> None:
    """Gate: hard 100%, soft >= 85% of the soft cases that ran. Names every failing case."""
    if any(c["id"] not in _RESULTS for c in CASES):
        pytest.skip("partial run (-k / -x): gate summary needs the full golden set")
    hard = [c["id"] for c in CASES if c["tier"] == "hard"]
    soft = [c["id"] for c in CASES if c["tier"] == "soft"]
    hard_bad = [i for i in hard if _RESULTS[i] != "passed"]
    soft_ran = [i for i in soft if _RESULTS[i] != "skipped"]
    soft_bad = [i for i in soft_ran if _RESULTS[i] == "failed"]
    soft_skipped = [i for i in soft if _RESULTS[i] == "skipped"]
    hard_pct = 100 * (len(hard) - len(hard_bad)) / len(hard)
    soft_pct = 100 * (len(soft_ran) - len(soft_bad)) / len(soft_ran) if soft_ran else 0.0
    print(
        f"\nGOLDEN GATE: HARD {hard_pct:.1f}% ({len(hard) - len(hard_bad)}/{len(hard)}) | "
        f"SOFT {soft_pct:.1f}% ({len(soft_ran) - len(soft_bad)}/{len(soft_ran)} ran, {len(soft_skipped)} skipped: no cassette)"
    )
    assert not hard_bad, f"HARD gate failed (must be 100%): {hard_bad}"
    assert soft_pct >= 85.0, f"SOFT gate failed (<85%): {soft_bad}"
