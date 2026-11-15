"""Chat page: a real conversation (message history, per-conversation session id, live progress)."""
import threading

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

from codeforge.store.conversations import ConversationNotFound
from codeforge.ui import handlers
from codeforge.ui.components import page_header

STEP_LABELS = {
    "ingress_guard": "Checking your request",
    "cache_lookup": "Looking for a verified earlier solution",
    "router": "Deciding what kind of task this is",
    "requirements": "Working out the requirements",
    "codegen": "Writing the code",
    "testgen": "Writing the tests",
    "test_validator": "Validating the tests",
    "policy_gate": "Running the safety policy",
    "human_approval": "Waiting for reviewer approval",
    "sandbox_run": "Running the tests in the sandbox",
    "evaluator": "Reviewing the result",
    "cache_store": "Saving the verified solution",
    "escalate_check": "Checking whether a ticket is needed",
    "report": "Writing the report",
}


def _on_feedback(message_id: int, thread_id: str) -> None:
    value = st.session_state.get(f"fb_{message_id}")
    if value is not None:
        handlers.record_feedback(thread_id, "up" if value == 1 else "down", "")
        st.toast("Thanks for the feedback!")


def _render_message(m) -> None:  # type: ignore[no-untyped-def]
    with st.chat_message(m.role):
        st.markdown(m.content)
        if m.code:
            with st.expander("Generated code", expanded=False):
                st.code(m.code, language="python")
        if m.tests:
            with st.expander("Generated tests"):
                st.code(m.tests, language="python")
        if m.role != "assistant" or not m.thread_id:
            return
        if m.status == "paused":
            st.info(f"Waiting for reviewer approval (run `{m.thread_id}`).")
            if st.button("Check status", key=f"chk_{m.id}"):
                handlers.refresh_turn(m.thread_id)
                st.rerun()
        elif m.status == "done":
            st.feedback("thumbs", key=f"fb_{m.id}", on_change=_on_feedback, args=(m.id, m.thread_id))
            st.caption(f"run `{m.thread_id}`" + (f" · served by {m.served_by}" if m.served_by else ""))


def _conversation_list() -> None:
    ss = st.session_state
    if st.button("➕ New chat", use_container_width=True, type="primary"):
        ss.conversation_id = None
        st.rerun()
    for conv_id, title in handlers.list_conversations():
        active = conv_id == ss.get("conversation_id")
        if st.button(title, key=f"conv_{conv_id}", use_container_width=True,
                     type="secondary" if not active else "primary", help=conv_id):
            ss.conversation_id = conv_id
            st.rerun()


def _run_turn(conv_id: str | None, prompt: str) -> str:
    """Run one turn with a live progress panel; returns the conversation id used."""
    ctx = get_script_run_ctx()
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"), st.status("Agents are working…", expanded=True) as status:

        def on_step(node: str) -> None:
            if ctx is not None:  # graph nodes may run on a worker thread
                add_script_run_ctx(threading.current_thread(), ctx)
            status.write(f"• {STEP_LABELS.get(node, node)}")

        turn = handlers.chat_turn(conv_id, prompt, on_step=on_step)
        status.update(label="Done" if turn.status in ("done", "paused") else "Something went wrong",
                      state="complete" if turn.status != "error" else "error", expanded=False)
    return turn.conversation_id


def render() -> None:
    ss = st.session_state
    ss.setdefault("conversation_id", None)
    page_header("Chat", "Describe what you want built, then keep talking to refine it. "
                        "Each conversation has its own session.")

    messages = []
    if ss.conversation_id:
        try:
            messages = handlers.load_messages(ss.conversation_id)
        except ConversationNotFound:  # e.g. profile switched: that conversation belongs to someone else
            ss.conversation_id = None

    side, main = st.columns([1, 3.4], gap="large")
    with side:
        st.markdown("**Conversations**")
        _conversation_list()
    prompt = st.chat_input("Ask for code, or follow up on the last answer…")
    with main:
        if ss.conversation_id:
            st.caption(f"Session `{ss.conversation_id}`")
        else:
            st.caption("New conversation — send a message to start.")
        for m in messages:
            _render_message(m)
        if prompt:
            ss.conversation_id = _run_turn(ss.conversation_id, prompt)
            st.rerun()
