"""Safe execution: policy gate verdicts, sandbox timeout, idempotent runs, honest reports, quota."""
import threading
import time
from typing import Any

import pytest
import yaml

import codeforge.pipeline.nodes as graph_module
from codeforge.config import Identity, current_identity
from codeforge.policy.code_policy import evaluate_code_policy
from codeforge.privacy.pii import mask_text
from codeforge.report import render_report
from codeforge.sandbox.runner import run_pytest_sandboxed
from codeforge.state import RunResult
from codeforge.store.db import init_db
from codeforge.store.runs import (
    QuotaExceeded,
    RunStore,
    compute_idempotency_key,
)
from evals.checks.common import (
    ROOT,
    Env,
)


def check_policy(verdict: str) -> Any:
    def handler(case: dict, env: Env) -> None:
        assert evaluate_code_policy(case["input"]).verdict == verdict

    return handler

def check_timeout(case: dict, env: Env) -> None:
    res = run_pytest_sandboxed(case["input"], "from solution import infinite\ndef test_inf(): infinite()", timeout_s=2)
    assert res.timed_out is True and res.exit_code is None

def check_idempotency(case: dict, env: Env) -> None:
    store = RunStore(init_db("sqlite:///:memory:"), lambda: Identity(user_id="u_alice", role="student"))
    key = compute_idempotency_key("u_alice", "code", "tests")
    store.save_run("r1", key, "req", "code", "tests", RunResult(exit_code=0, passed=2))
    store.save_run("r2", key, "req", "code", "tests", RunResult(exit_code=1, failed=1))  # must not overwrite
    cached = store.get_by_idem_key(key)
    assert cached is not None and cached.cached is True and cached.exit_code == 0

def check_concurrent_idempotent(case: dict, env: Env) -> None:
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

def check_report_honesty(case: dict, env: Env) -> None:
    rep = render_report({"run_result": RunResult(exit_code=1, failed=1, last_failing_test="test_x")})
    assert "Tests passed" not in rep and "❌ Tests failed" in rep
    assert "Tests passed" not in render_report({"run_result": RunResult(exit_code=None, timed_out=True)})

def check_quota(case: dict, env: Env) -> None:
    store = RunStore(init_db("sqlite:///:memory:"), lambda: Identity(user_id="u_alice", role="student"))
    for i in range(20):
        store.save_run(f"r{i}", f"k{i}", "req", "c", "t", RunResult(exit_code=0))
    with pytest.raises(QuotaExceeded):
        store.check_and_increment_quota()

def check_redteam_corpus(case: dict, env: Env) -> None:
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


CHECKS: dict[str, Any] = {
    "policy_deny": check_policy("deny"),
    "policy_approval": check_policy("needs_approval"),
    "policy_allow": check_policy("allow"),
    "timeout": check_timeout,
    "idempotency": check_idempotency,
    "concurrent_idempotent": check_concurrent_idempotent,
    "report_honesty": check_report_honesty,
    "quota_enforced": check_quota,
    "redteam_corpus": check_redteam_corpus,
}
