"""What each button in the web UI does. Plain functions: no UI-framework imports, easy to unit test.

Every handler works as the identity from config (see codeforge.config.current_identity).
"""
import json
import os
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func

from codeforge.config import clear_settings_cache, current_identity, get_settings
from codeforge.entrypoint import SubmitResult, submit
from codeforge.hitl.approval import expire_stale, resume_approval
from codeforge.llm import breaker_states
from codeforge.observability import get_logger, score_trace
from codeforge.pipeline import build_graph
from codeforge.pipeline.tracing import step_listener
from codeforge.privacy.pii import mask_text
from codeforge.report import is_refused
from codeforge.store.conversations import ConversationStore
from codeforge.store.db import Approval, Feedback, Message, Metric, Ticket, init_db
from codeforge.store.runs import RunStore

chat_log = get_logger("chat")


def _reply_from(result: SubmitResult) -> tuple[str, str, str, str]:
    """(reply markdown, code, tests, status) for a finished or paused pipeline run."""
    values = result.values
    code, tests = values.get("code", ""), values.get("tests", "")
    if result.pending_approval:
        policy = values.get("policy")
        reasons = "; ".join(policy.reasons) if policy else "File/Network access requested"
        msg = (
            f"### ⏸ Execution Paused: Reviewer Approval Required\n"
            f"- **Thread ID**: `{result.thread_id}`\n"
            f"- **Policy Reasons**: {reasons}\n"
            f"Please switch to the **Reviewer Queue** page as an instructor to Approve or Reject this run."
        )
        return msg, code, tests, "paused"
    if is_refused(values):
        code = tests = ""  # refused code is never shown or replayed as chat history
    return values.get("report", "No report generated."), code, tests, "done"


def submit_request(prompt: str, thread_id: str | None = None) -> tuple[str, str, str, str]:
    if not prompt or not prompt.strip():
        return "Please enter a valid programming request.", "", "", ""

    result = submit(prompt, thread_id=thread_id or None)
    reply, code, tests, _status = _reply_from(result)
    return reply, code, tests, result.thread_id


# ---------------------------------------------------------------- chat conversations


@dataclass
class ChatTurn:
    conversation_id: str
    reply: str
    code: str = ""
    tests: str = ""
    thread_id: str = ""
    status: str = "done"  # done | paused | error | invalid
    served_by: str = ""
    trace_id: str = ""


def _log_message(msg: Message) -> None:
    chat_log.info(
        "chat_message",
        conversation_id=msg.conversation_id,
        message_id=msg.id,
        role=msg.role,
        thread_id=msg.thread_id,
        status=msg.status,
        content=msg.content,
    )


def chat_turn(
    conversation_id: str | None, text: str, on_step: Callable[[str], None] | None = None
) -> ChatTurn:
    """One user message -> one pipeline run -> one assistant message, inside a conversation.

    Both messages are stored (masked) and logged; the conversation id is the Langfuse session id.
    ``on_step`` is told each pipeline node as it starts (for live progress in the UI).
    """
    if not text or not text.strip():
        return ChatTurn(conversation_id or "", "Please enter a valid programming request.", status="invalid")

    store = ConversationStore()
    conv_id = conversation_id or store.create()
    history = store.history(conv_id)  # also verifies ownership; excludes the message being sent
    _log_message(store.append(conv_id, "user", mask_text(text)))

    token = step_listener.set(on_step)
    try:
        result = submit(text, history=history or None, session_id=conv_id)
        reply, code, tests, status = _reply_from(result)
        thread_id, served_by, trace_id = result.thread_id, str(result.values.get("served_by", "")), result.trace_id
    except Exception as exc:  # noqa: BLE001 - a failed turn must still be answered and recorded
        chat_log.exception("chat_turn_failed", conversation_id=conv_id)
        reply, code, tests, status = f"Sorry, this request failed ({type(exc).__name__}). Please try again.", "", "", "error"
        thread_id = served_by = trace_id = ""
    finally:
        step_listener.reset(token)

    _log_message(
        store.append(
            conv_id, "assistant", reply, code=code, tests=tests, thread_id=thread_id,
            status=status, served_by=served_by, trace_id=trace_id,
        )
    )
    return ChatTurn(conv_id, reply, code, tests, thread_id, status, served_by, trace_id)


def new_conversation() -> str:
    return ConversationStore().create()


def list_conversations() -> list[tuple[str, str]]:
    """(conversation id, title) for the current user, most recently active first."""
    return [(c.id, c.title) for c in ConversationStore().list_mine()]


def load_messages(conversation_id: str) -> list[Message]:
    return ConversationStore().messages(conversation_id)


def refresh_turn(thread_id: str) -> str:
    """Pick up the outcome of a turn that was paused for approval; returns its current status."""
    graph = build_graph()
    snap = graph.get_state({"configurable": {"thread_id": thread_id}})
    if not snap.values:
        return "unknown"
    if "human_approval" in tuple(snap.next):
        return "paused"
    values = snap.values
    refused = is_refused(values)  # a rejected/denied run must not reveal its code
    ConversationStore().update_assistant_message(
        thread_id,
        content=values.get("report", "No report generated."),
        code="" if refused else values.get("code", ""),
        tests="" if refused else values.get("tests", ""),
        status="done",
        served_by=str(values.get("served_by", "")),
    )
    return "done"


def record_feedback(run_id: str, thumb: str, comment: str) -> str:
    if not run_id:
        return "No active run to record feedback for."
    try:
        sf = init_db()
        with sf() as session:
            fb = Feedback(run_id=run_id, thumb=thumb, comment=comment)
            session.add(fb)
            session.commit()
            msg = session.query(Message).filter(Message.thread_id == run_id, Message.role == "assistant").first()
            if msg is not None and msg.trace_id:
                score_trace(msg.trace_id, "user_feedback", 1.0 if thumb == "up" else 0.0, comment)
        return f"Feedback recorded ({thumb})! Thank you."
    except Exception as e:  # noqa: BLE001
        return f"Error recording feedback: {e}"


def my_runs_table() -> list[list[str]]:
    store = RunStore()
    runs = store.get_my_runs()
    rows = []
    for r in runs:
        res = json.loads(r.result_json) if r.result_json else {}
        rows.append([
            r.run_id,
            r.owner,
            r.request_masked[:40],
            str(res.get("passed", 0)),
            str(res.get("failed", 0)),
            str(res.get("exit_code", "-")),
            str(r.created_at)[:19],
        ])
    return rows


def pending_approvals_table() -> list[list[str]]:
    sf = init_db()
    if current_identity().role != "instructor":
        return []
    expire_stale(build_graph())
    with sf() as session:
        approvals = session.query(Approval).filter(Approval.verdict == "pending").all()
        return [
            [a.thread_id, a.requester_id, a.reasons[:50], a.code_hash, str(a.created_at)[:19]]
            for a in approvals
        ]


def review_decision(thread_id: str, action: str, comment: str) -> str:
    """Approve/Reject. The approver identity comes from server config inside resume_approval."""
    approved = action == "Approve"
    graph = build_graph()
    try:
        resume_approval(graph, thread_id, approved, comment)
        return f"Decision '{action}' applied to thread {thread_id}."
    except PermissionError as e:
        return f"⛔ Not permitted: {e}"
    except Exception as e:  # noqa: BLE001
        return f"Failed to resume thread: {e}"


def tickets_table() -> list[list[str]]:
    sf = init_db()
    with sf() as session:
        tickets = session.query(Ticket).order_by(Ticket.created_at.desc()).all()
        return [
            [t.ticket_id, t.priority, str(t.sla_hours), t.request_masked[:50], t.reason[:40], str(t.created_at)[:19]]
            for t in tickets
        ]


def ops_summary() -> str:
    """Per-agent cost/latency from the metering table plus live circuit-breaker states."""
    with init_db()() as session:
        rows = (
            session.query(
                Metric.agent,
                func.count(Metric.id),
                func.sum(Metric.input_tokens),
                func.sum(Metric.output_tokens),
                func.sum(Metric.cost_usd),
                func.avg(Metric.latency_s),
            )
            .group_by(Metric.agent)
            .all()
        )
    lines = [
        "### Ops Telemetry",
        "",
        "| Agent | Calls | In tok | Out tok | Cost (USD) | Avg latency (s) |",
        "|---|---|---|---|---|---|",
    ]
    total = 0.0
    for agent, calls, tin, tout, cost, lat in rows:
        total += cost or 0.0
        lines.append(f"| {agent} | {calls} | {tin or 0} | {tout or 0} | ${cost or 0:.6f} | {lat or 0:.2f} |")
    lines += ["", f"**Total cost**: ${total:.6f}", ""]
    states = breaker_states()
    lines.append("**Circuit breakers**: " + (", ".join(f"`{k}`={v}" for k, v in states.items()) or "none yet"))
    lines.append(f"\n**Chaos failure rate**: {get_settings().chaos_failure_rate:.0%}")
    return "\n".join(lines)


def set_chaos_rate(rate: float) -> str:
    """Instructor-only chaos switch (demo): fail this fraction of model calls."""
    if current_identity().role != "instructor":
        return "⛔ Only instructors can change the chaos setting."
    os.environ["CHAOS_FAILURE_RATE"] = str(rate)
    clear_settings_cache()
    return f"Chaos failure rate set to {rate:.0%}."


def identity_badge() -> str:
    ident = current_identity()
    return f"**Active Identity**: `[{ident.user_id}]` | **Role**: `{ident.role}`"


def switch_profile(profile_name: str) -> tuple[str, bool]:
    """Demo-only profile switch (identity still comes from config profiles, never from text).

    Returns (identity badge text, whether to show the reviewer tab).
    """
    os.environ["CODEFORGE_PROFILE"] = profile_name
    clear_settings_cache()
    return identity_badge(), current_identity().role == "instructor"
