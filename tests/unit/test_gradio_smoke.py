from codeforge.ui import handlers
from codeforge.ui.layout import build_app
from tests.helpers import outage


def test_app_builds_and_handlers_work_offline(monkeypatch):
    outage(monkeypatch)

    assert build_app() is not None

    report, code, _tests, tid = handlers.submit_request("Write an is_palindrome function")
    assert "✅ Tests passed" in report and "def is_palindrome" in code and tid.startswith("run_")

    assert handlers.submit_request("   ")[0].startswith("Please enter")
    assert len(handlers.my_runs_table()) == 1
    assert handlers.tickets_table() == []
    assert "Circuit breakers" in handlers.ops_summary()


def test_student_cannot_use_reviewer_or_chaos_controls():
    assert "Not permitted" in handlers.review_decision("nope", "Approve", "")
    assert handlers.pending_approvals_table() == []
    assert "Only instructors" in handlers.set_chaos_rate(0.5)


def test_instructor_can_set_chaos_and_profile_switch_controls_reviewer_tab(as_profile, monkeypatch):
    monkeypatch.setenv("CHAOS_FAILURE_RATE", "0")  # restored by monkeypatch at teardown
    as_profile("instructor")
    assert "set to 50%" in handlers.set_chaos_rate(0.5)
    badge, show_reviewer_tab = handlers.switch_profile("student_a")
    assert "student" in badge and show_reviewer_tab is False
    badge, show_reviewer_tab = handlers.switch_profile("instructor")
    assert show_reviewer_tab is True
