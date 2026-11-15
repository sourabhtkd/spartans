import codeforge.app_gradio as ui
from codeforge.llm import AllModelsDown


def test_app_builds_and_handlers_work_offline(monkeypatch):
    def down(agent, messages, schema):
        raise AllModelsDown("down")

    for a in ("router", "requirements", "codegen", "testgen"):
        monkeypatch.setattr(f"codeforge.agents.{a}.resilient_invoke", down)

    assert ui.build_app() is not None

    report, code, _tests, tid = ui.submit_request("Write an is_palindrome function")
    assert "✅ Tests passed" in report and "def is_palindrome" in code and tid.startswith("run_")

    assert ui.submit_request("   ")[0].startswith("Please enter")
    runs = ui.get_my_runs_table()
    assert len(runs) == 1
    assert ui.get_tickets_table() == []
    assert "Circuit breakers" in ui.get_ops_metrics()


def test_student_cannot_use_reviewer_or_chaos_controls():
    assert "Not permitted" in ui.process_review_decision("nope", "Approve", "")
    assert ui.get_pending_approvals() == []
    assert "Only instructors" in ui.set_chaos(0.5)


def test_instructor_can_set_chaos_and_profile_switch_hides_reviewer_tab(as_profile, monkeypatch):
    monkeypatch.setenv("CHAOS_FAILURE_RATE", "0")  # restored by monkeypatch at teardown
    as_profile("instructor")
    assert "set to 50%" in ui.set_chaos(0.5)
    badge, tab_update = ui.update_profile("student_a")
    assert "student" in badge and tab_update["visible"] is False
    badge, tab_update = ui.update_profile("instructor")
    assert tab_update["visible"] is True
