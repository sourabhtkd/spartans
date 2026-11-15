import pytest

from codeforge.store.conversations import ConversationNotFound, ConversationStore


def test_create_append_list_and_history():
    store = ConversationStore()
    conv = store.create()
    assert conv.startswith("conv_")

    store.append(conv, "user", "Write is_palindrome please")
    store.append(conv, "assistant", "✅ done", code="def is_palindrome(s): ...", thread_id="run_1")

    assert [m.role for m in store.messages(conv)] == ["user", "assistant"]
    assert store.list_mine()[0].title == "Write is_palindrome please"
    history = store.history(conv)
    assert history[0] == {"role": "user", "content": "Write is_palindrome please"}
    assert history[1]["role"] == "assistant" and "def is_palindrome" in history[1]["content"]


def test_conversations_are_owner_scoped(as_profile):
    store = ConversationStore()
    conv = store.create()
    store.append(conv, "user", "secret plan")

    as_profile("student_b")
    assert ConversationStore().list_mine() == []
    with pytest.raises(ConversationNotFound):
        ConversationStore().messages(conv)
    with pytest.raises(ConversationNotFound):
        ConversationStore().append(conv, "user", "hi")


def test_update_assistant_message_by_thread():
    store = ConversationStore()
    conv = store.create()
    store.append(conv, "assistant", "paused", thread_id="run_p", status="paused")
    assert store.update_assistant_message("run_p", content="finished", status="done")
    msg = store.messages(conv)[0]
    assert (msg.content, msg.status) == ("finished", "done")
    assert not store.update_assistant_message("run_missing", content="x")
