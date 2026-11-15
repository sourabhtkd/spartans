"""What each button in the web UI does. Plain functions: no Gradio imports, easy to unit test.

Every handler works as the identity from config (see codeforge.config.current_identity).
"""
import json
import os

from sqlalchemy import func

from codeforge.config import clear_settings_cache, current_identity, get_settings
from codeforge.entrypoint import submit
from codeforge.hitl.approval import expire_stale, resume_approval
from codeforge.llm import breaker_states
from codeforge.pipeline import build_graph
from codeforge.store.db import Approval, Feedback, Metric, Ticket, init_db
from codeforge.store.runs import RunStore


def submit_request(prompt: str, thread_id: str | None = None) -> tuple[str, str, str, str]:
    if not prompt or not prompt.strip():
        return "Please enter a valid programming request.", "", "", ""

    result = submit(prompt, thread_id=thread_id or None)
    tid = result.thread_id
    values = result.values

    if result.pending_approval:
        policy = values.get("policy")
        reasons = "; ".join(policy.reasons) if policy else "File/Network access requested"
        msg = (
            f"### ⏸ Execution Paused: Reviewer Approval Required\n"
            f"- **Thread ID**: `{tid}`\n"
            f"- **Policy Reasons**: {reasons}\n"
            f"Please switch to the **Reviewer Queue** tab as an instructor to Approve or Reject this run."
        )
        return msg, values.get("code", ""), values.get("tests", ""), tid

    return (
        values.get("report", "No report generated."),
        values.get("code", ""),
        values.get("tests", ""),
        tid,
    )


def record_feedback(run_id: str, thumb: str, comment: str) -> str:
    if not run_id:
        return "No active run to record feedback for."
    try:
        sf = init_db()
        with sf() as session:
            fb = Feedback(run_id=run_id, thumb=thumb, comment=comment)
            session.add(fb)
            session.commit()
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
