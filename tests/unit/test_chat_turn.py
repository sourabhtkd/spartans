import json

from codeforge.pipeline.tracing import step_listener
from codeforge.store.conversations import ConversationStore
from codeforge.ui import handlers
from tests.helpers import outage


def _log_events(capsys, name):
    err = capsys.readouterr().err
    return [e for e in (json.loads(line) for line in err.splitlines() if line.startswith("{")) if e["event"] == name]


def test_two_turns_share_one_conversation_with_distinct_runs(monkeypatch):
    outage(monkeypatch)
    first = handlers.chat_turn(None, "Write an is_palindrome function")
    second = handlers.chat_turn(first.conversation_id, "Write an is_palindrome function again")

    assert first.conversation_id == second.conversation_id
    assert first.thread_id != second.thread_id and first.status == second.status == "done"
    msgs = handlers.load_messages(first.conversation_id)
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[1].thread_id == first.thread_id and "def is_palindrome" in msgs[1].code
    assert handlers.list_conversations() == [(first.conversation_id, "Write an is_palindrome function")]


def test_later_turns_receive_history_and_session_id(monkeypatch):
    seen = []
    real_submit = handlers.submit

    def spy(text, **kwargs):
        seen.append(kwargs)
        return real_submit(text, **kwargs)

    outage(monkeypatch)
    monkeypatch.setattr(handlers, "submit", spy)
    t1 = handlers.chat_turn(None, "Write an is_palindrome function")
    handlers.chat_turn(t1.conversation_id, "now add type hints")

    assert seen[0]["history"] is None and seen[0]["session_id"] == t1.conversation_id
    assert [h["role"] for h in seen[1]["history"]] == ["user", "assistant"]
    assert "def is_palindrome" in seen[1]["history"][1]["content"]


def test_every_message_is_logged_masked_with_session(monkeypatch, capsys):
    outage(monkeypatch)
    secret = "sk-abcdefghijklmnop1234"  # gitleaks:allow
    turn = handlers.chat_turn(None, f"Write an is_palindrome function, my key {secret}")
    err = capsys.readouterr()
    lines = [json.loads(line) for line in err.err.splitlines() if line.startswith("{")]
    chat = [e for e in lines if e["event"] == "chat_message"]

    assert [e["role"] for e in chat] == ["user", "assistant"]
    assert all(e["conversation_id"] == turn.conversation_id for e in chat)
    assert secret not in err.err
    # pipeline logs of the same turn carry the session id as well
    assert any(e.get("session_id") == turn.conversation_id for e in lines if e["event"] == "node_end")
    assert ConversationStore().messages(turn.conversation_id)[0].content.count(secret) == 0


def test_step_listener_reports_nodes_in_order(monkeypatch):
    outage(monkeypatch)
    steps: list[str] = []
    handlers.chat_turn(None, "Write an is_palindrome function", on_step=steps.append)
    assert steps[0] == "ingress_guard" and "sandbox_run" in steps and steps[-1] == "report"
    assert step_listener.get() is None  # reset after the turn


def test_blank_message_is_rejected_without_storing():
    turn = handlers.chat_turn(None, "   ")
    assert turn.status == "invalid"
    assert handlers.list_conversations() == []


def test_failed_turn_is_answered_and_recorded(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(handlers, "submit", boom)
    turn = handlers.chat_turn(None, "Write something")
    assert turn.status == "error" and "failed" in turn.reply
    assert [m.status for m in handlers.load_messages(turn.conversation_id)][-1] == "error"


def test_paused_turn_is_stored_as_paused_and_refresh_picks_up_outcome(monkeypatch):
    from codeforge.entrypoint import SubmitResult

    def paused(text, **kwargs):
        return SubmitResult("run_pause1", {"code": "import os", "tests": "", "policy": None}, ("human_approval",))

    monkeypatch.setattr(handlers, "submit", paused)
    turn = handlers.chat_turn(None, "read a file")
    assert turn.status == "paused" and "Reviewer Approval" in turn.reply
    assert handlers.load_messages(turn.conversation_id)[-1].status == "paused"


def test_reply_hides_code_when_policy_denied():
    from types import SimpleNamespace

    from codeforge.state import PolicyDecision

    values = {
        "code": "import os\n",
        "tests": "def test_x(): pass\n",
        "policy": PolicyDecision(verdict="deny", reasons=["Denied import 'os'"]),
        "report": "❌ Refused by policy: Denied import 'os'. Nothing was executed.",
    }
    reply, code, tests, status = handlers._reply_from(SimpleNamespace(values=values, pending_approval=False))
    assert "Refused by policy" in reply and code == "" and tests == "" and status == "done"
