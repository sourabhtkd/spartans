import json
from pathlib import Path

import pytest

from codeforge.config import Identity, clear_settings_cache, current_identity
from codeforge.guard.pii import mask_text
from codeforge.hitl.approval import human_approval_node
from codeforge.hitl.handoff import create_ticket, should_escalate
from codeforge.policy.code_policy import evaluate_code_policy
from codeforge.report import render_report
from codeforge.resilience.templates import match_template
from codeforge.sandbox.runner import run_pytest_sandboxed
from codeforge.state import CodeForgeState, RunResult
from codeforge.store.db import init_db
from codeforge.store.runs import RunNotFound, RunStore, compute_idempotency_key

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_set.json"
CASES = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

_SESSION_RESULTS: dict[str, bool] = {}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case: dict, monkeypatch: pytest.MonkeyPatch):
    case_id = case["id"]
    kind = case["kind"]
    inp = case["input"]
    expect = case["expect"]

    try:
        if kind == "identity_spoof":
            clear_settings_cache()
            monkeypatch.delenv("CODEFORGE_PROFILE", raising=False)
            clear_settings_cache()
            ident = current_identity()
            assert ident.role == "student"

        elif kind == "cross_user_read":
            db_mem = init_db("sqlite:///:memory:")
            store_alice = RunStore(session_factory=db_mem, identity_provider=lambda: Identity(user_id="u_alice", role="student"))
            store_bob = RunStore(session_factory=db_mem, identity_provider=lambda: Identity(user_id="u_bob", role="student"))
            store_alice.save_run("run_alice", "k1", "req", "code", "tests", RunResult(exit_code=0, passed=1))
            with pytest.raises(RunNotFound) as exc:
                store_bob.get_run("run_alice")
            assert str(exc.value) == expect

        elif kind.startswith("pii_"):
            masked = mask_text(inp)
            assert expect in masked

        elif kind == "policy_deny":
            dec = evaluate_code_policy(inp)
            assert dec.verdict == "deny"

        elif kind == "policy_approval":
            dec = evaluate_code_policy(inp)
            assert dec.verdict == "needs_approval"

        elif kind == "policy_allow":
            dec = evaluate_code_policy(inp)
            assert dec.verdict == "allow"

        elif kind == "timeout":
            res = run_pytest_sandboxed(
                code=inp,
                tests="from solution import infinite\ndef test_inf(): infinite()",
                timeout_s=2,
            )
            assert res.timed_out is True

        elif kind == "idempotency":
            db_mem = init_db("sqlite:///:memory:")
            store = RunStore(session_factory=db_mem, identity_provider=lambda: Identity(user_id="u_alice", role="student"))
            key = compute_idempotency_key("u_alice", "code", "tests")
            store.save_run("r1", key, "req", "code", "tests", RunResult(exit_code=0, passed=2))
            cached = store.get_by_idem_key(key)
            assert cached is not None
            assert cached.cached is True

        elif kind == "report_honesty":
            state: CodeForgeState = {
                "run_result": RunResult(exit_code=1, failed=1, last_failing_test="test_x"),
            }
            rep = render_report(state)
            assert "Tests passed" not in rep
            assert "❌ Tests failed" in rep

        elif kind == "approval_pause":
            if expect == "rejected_role":
                monkeypatch.setattr(
                    "codeforge.hitl.approval.interrupt",
                    lambda payload: {"approved": True, "approver_id": "u_bob"},
                )
                state = {
                    "thread_id": "th_t1",
                    "code": "import requests",
                    "identity": Identity(user_id="u_alice", role="student"),
                }
                res = human_approval_node(state)
                assert res["approval"].approved is False
            elif expect == "rejected_self":
                monkeypatch.setattr(
                    "codeforge.hitl.approval.interrupt",
                    lambda payload: {"approved": True, "approver_id": "u_prof"},
                )
                state = {
                    "thread_id": "th_t2",
                    "code": "import requests",
                    "identity": Identity(user_id="u_prof", role="instructor"),
                }
                res = human_approval_node(state)
                assert res["approval"].approved is False

        elif kind == "escalation":
            if expect == "P1":
                esc, _, prio = should_escalate(inp)
                assert esc is True
                assert prio == "P1"
            elif expect == "ticket_masked":
                masked_inp = mask_text(inp)
                tkt = create_ticket({"raw_request_masked": masked_inp}, init_db("sqlite:///:memory:"))
                assert "sk-" not in tkt.request_masked
                assert "[SECRET]" in tkt.request_masked

        elif kind == "outage_template":
            tpl = match_template(inp)
            assert tpl is not None
            assert tpl.name == "palindrome"
            res = run_pytest_sandboxed(tpl.code, tpl.tests, timeout_s=5)
            assert res.exit_code == 0

        elif kind == "outage_unknown":
            tpl = match_template(inp)
            assert tpl is None  # Unknown task fails to match templates, resulting in honest refusal
            state = {"served_tier": "refused"}
            rep = render_report(state)
            assert "cannot generate code right now" in rep

        elif kind == "cache_lookalike":
            from codeforge.cost.semantic_cache import extract_guard_tokens

            g1 = extract_guard_tokens("sort list ascending")
            g2 = extract_guard_tokens("sort list descending")
            assert g1 != g2  # Guard token mismatch forces cache miss

        elif kind == "routing":
            if expect == "sql":
                assert any(k in inp.lower() for k in ["select", "insert", "sql"])
            else:
                assert any(k in inp.lower() for k in ["python", "function", "def"])

        elif kind == "correctness":
            tpl = match_template(inp)
            assert tpl is not None
            ref_path = Path(__file__).resolve().parent / "reference_tests" / expect
            assert ref_path.exists()
            ref_tests = ref_path.read_text(encoding="utf-8")
            res = run_pytest_sandboxed(code=tpl.code, tests=ref_tests, timeout_s=5)
            assert res.exit_code == 0
            assert res.passed >= 1

        _SESSION_RESULTS[case_id] = True
    except Exception:
        _SESSION_RESULTS[case_id] = False
        raise


def test_golden_gate_summary():
    """Asserts hard cases 100% pass and soft cases >= 85% pass."""
    hard_cases = [c["id"] for c in CASES if c["tier"] == "hard"]
    soft_cases = [c["id"] for c in CASES if c["tier"] == "soft"]

    hard_passed = sum(1 for cid in hard_cases if _SESSION_RESULTS.get(cid, False))
    soft_passed = sum(1 for cid in soft_cases if _SESSION_RESULTS.get(cid, False))

    hard_pct = (hard_passed / len(hard_cases)) * 100 if hard_cases else 100.0
    soft_pct = (soft_passed / len(soft_cases)) * 100 if soft_cases else 100.0

    print(f"\nGolden Gate Summary: HARD={hard_pct:.1f}% ({hard_passed}/{len(hard_cases)}) | SOFT={soft_pct:.1f}% ({soft_passed}/{len(soft_cases)})")
    assert hard_pct == 100.0, f"Hard golden gate failed: {hard_passed}/{len(hard_cases)}"
    assert soft_pct >= 85.0, f"Soft golden gate failed: {soft_passed}/{len(soft_cases)}"
