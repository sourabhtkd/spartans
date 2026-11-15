from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from codeforge.hitl.handoff import create_ticket
from codeforge.ui import handlers
from tests.helpers import outage

APP = str(Path(__file__).parents[2] / "codeforge" / "ui" / "app.py")


def test_app_renders_and_handlers_work_offline(monkeypatch):
    outage(monkeypatch)

    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "Tickets" not in [link.value for link in at.page_link]
    assert not at.radio

    report, code, _tests, tid = handlers.submit_request("Write an is_palindrome function")
    assert "✅ Tests passed" in report and "def is_palindrome" in code and tid.startswith("run_")

    assert handlers.submit_request("   ")[0].startswith("Please enter")
    assert len(handlers.my_runs_table()) == 1
    with pytest.raises(PermissionError, match="Only instructors"):
        handlers.tickets_table()
    assert "Circuit breakers" in handlers.ops_summary()


def test_student_cannot_use_reviewer_or_chaos_controls():
    assert "Not permitted" in handlers.review_decision("nope", "Approve", "")
    assert handlers.pending_approvals_table() == []
    assert "Only instructors" in handlers.set_chaos_rate(0.5)


def test_instructor_can_set_chaos(as_profile, monkeypatch):
    monkeypatch.setenv("CHAOS_FAILURE_RATE", "0")  # restored by monkeypatch at teardown
    as_profile("instructor")
    assert "set to 50%" in handlers.set_chaos_rate(0.5)


def test_app_renders_for_instructor(as_profile):
    as_profile("instructor")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "Tickets" in [link.value for link in at.page_link]


def test_ticket_listing_is_instructor_only(as_profile):
    ticket = create_ticket({"request_masked": "student request"})

    with pytest.raises(PermissionError, match="Only instructors"):
        handlers.tickets_table()

    as_profile("instructor")
    assert any(row[0] == ticket.ticket_id for row in handlers.tickets_table())


def _tickets_page():
    from codeforge.ui.pages import tickets

    tickets.render()


def test_student_cannot_render_tickets_page(monkeypatch):
    monkeypatch.setattr(handlers, "tickets_table", lambda: pytest.fail("Student accessed ticket data"))
    at = AppTest.from_function(_tickets_page).run()
    assert not at.exception
    assert any("Only instructors" in e.value for e in at.error)


def _chat_page():
    from codeforge.ui.pages import chat

    chat.render()


def test_chat_page_is_a_conversation(monkeypatch):
    # The chat page is driven directly: AppTest cannot re-run an app that uses st.navigation.
    outage(monkeypatch)
    at = AppTest.from_function(_chat_page, default_timeout=60).run()
    assert not at.exception and len(at.chat_message) == 0

    at.chat_input[0].set_value("Write an is_palindrome function").run()
    assert not at.exception
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    conv_id = at.session_state["conversation_id"]
    assert conv_id.startswith("conv_")

    at.chat_input[0].set_value("Write an is_palindrome function once more").run()
    assert not at.exception
    assert [m.name for m in at.chat_message] == ["user", "assistant", "user", "assistant"]
    assert at.session_state["conversation_id"] == conv_id
    assert any(conv_id in c.value for c in at.caption)
