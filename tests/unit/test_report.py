from codeforge.report import render_report
from codeforge.state import (
    ApprovalDecision,
    CodeForgeState,
    HandoffTicket,
    PolicyDecision,
    RunResult,
)


def test_report_tests_passed_only_on_exit_code_zero():
    # exit_code == 0 -> tests passed
    state_pass: CodeForgeState = {
        "run_result": RunResult(exit_code=0, passed=3, failed=0),
    }
    report_pass = render_report(state_pass)
    assert "✅ Tests passed (3/3)" in report_pass

    # exit_code == 1 -> tests failed
    state_fail: CodeForgeState = {
        "run_result": RunResult(exit_code=1, passed=2, failed=1, last_failing_test="test_x"),
    }
    report_fail = render_report(state_fail)
    assert "Tests passed" not in report_fail
    assert "❌ Tests failed" in report_fail
    assert "test_x" in report_fail

    # timed_out -> killed, never tests passed
    state_timeout: CodeForgeState = {
        "run_result": RunResult(exit_code=None, timed_out=True),
    }
    report_timeout = render_report(state_timeout)
    assert "Tests passed" not in report_timeout
    assert "⏱ Tests did not finish" in report_timeout


def test_report_policy_denied():
    state_denied: CodeForgeState = {
        "policy": PolicyDecision(verdict="deny", reasons=["Denied import 'os'"]),
    }
    rep = render_report(state_denied)
    assert "❌ Refused by policy: Denied import 'os'" in rep
    assert "Tests passed" not in rep


def test_report_reviewer_rejected():
    state_rej: CodeForgeState = {
        "approval": ApprovalDecision(approved=False, approver_id="u_prof", comment="Unsafe write"),
    }
    rep = render_report(state_rej)
    assert "Reviewer rejected: Unsafe write" in rep
    assert "Tests passed" not in rep


def test_report_includes_metadata_and_ticket():
    ticket = HandoffTicket(
        ticket_id="tkt_12345",
        priority="P1",
        sla_hours=4,
        request_masked="masked",
        attempts=2,
        reason="Human escalation",
    )
    res = RunResult(exit_code=0, passed=2, failed=0, cached=True)
    state: CodeForgeState = {
        "run_result": res,
        "served_by": "template",
        "cache_hit": True,
        "ticket": ticket,
        "code": "def foo(): pass",
        "tests": "def test_foo(): pass",
    }
    rep = render_report(state)
    assert "tkt_12345" in rep
    assert "template" in rep
    assert "Idempotency Note" in rep
    assert "Semantic Cache Hit" in rep


def test_report_hides_code_when_policy_denied():
    state: CodeForgeState = {
        "code": "import os\n",
        "tests": "def test_x(): pass\n",
        "policy": PolicyDecision(verdict="deny", reasons=["Denied import 'os'"]),
    }
    report = render_report(state)
    assert "Refused by policy" in report and "Denied import 'os'" in report
    assert "Generated Solution" not in report and "Generated Tests" not in report
    assert "import os" not in report


def test_reviewer_rejected_code_is_hidden():
    from codeforge.report import is_refused, render_report
    from codeforge.state import ApprovalDecision, PolicyDecision

    state = {
        "code": "SECRET_CODE = 1",
        "tests": "def test_x(): pass",
        "policy": PolicyDecision(verdict="needs_approval", reasons=["network"]),
        "approval": ApprovalDecision(approved=False, approver_id="i1", comment="no"),
    }
    assert is_refused(state)
    assert "SECRET_CODE" not in render_report(state)
