import json
import os
from typing import Any

import gradio as gr

from codeforge.config import clear_settings_cache, current_identity, get_settings
from codeforge.entrypoint import submit
from codeforge.graph import build_graph
from codeforge.hitl.approval import expire_stale, resume_approval
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


def submit_feedback(run_id: str, thumb: str, comment: str) -> str:
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


def get_my_runs_table() -> list[list[str]]:
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


def get_pending_approvals() -> list[list[str]]:
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


def process_review_decision(thread_id: str, action: str, comment: str) -> str:
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


def get_tickets_table() -> list[list[str]]:
    sf = init_db()
    with sf() as session:
        tickets = session.query(Ticket).order_by(Ticket.created_at.desc()).all()
        return [
            [t.ticket_id, t.priority, str(t.sla_hours), t.request_masked[:50], t.reason[:40], str(t.created_at)[:19]]
            for t in tickets
        ]


def get_ops_metrics() -> str:
    """Per-agent cost/latency from the metering table plus live circuit-breaker states."""
    from sqlalchemy import func

    from codeforge.llm import breaker_states

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


def set_chaos(rate: float) -> str:
    """Instructor-only chaos switch (demo): fail this fraction of model calls."""
    if current_identity().role != "instructor":
        return "⛔ Only instructors can change the chaos setting."
    os.environ["CHAOS_FAILURE_RATE"] = str(rate)
    clear_settings_cache()
    return f"Chaos failure rate set to {rate:.0%}."


def update_profile(profile_name: str) -> tuple[str, Any]:
    """Demo-only profile switch (identity still comes from config profiles, never from text)."""
    os.environ["CODEFORGE_PROFILE"] = profile_name
    clear_settings_cache()
    ident = current_identity()
    return (
        f"**Active Identity**: `[{ident.user_id}]` | **Role**: `{ident.role}`",
        gr.update(visible=ident.role == "instructor"),
    )


def build_app() -> gr.Blocks:
    settings = get_settings()
    initial_ident = current_identity()

    with gr.Blocks(title="CodeForge — Multi-Agent NL to Code") as demo:
        gr.Markdown("# 🔨 CodeForge: Correct, Safe & Honest Multi-Agent Engine")

        with gr.Row():
            profile_dropdown = gr.Dropdown(
                label="Switch User Profile",
                choices=list(settings.profiles.keys()),
                value=settings.active_profile,
            )
            profile_badge = gr.Markdown(
                f"**Active Identity**: `[{initial_ident.user_id}]` | **Role**: `{initial_ident.role}`"
            )

        with gr.Tabs():
            # 1. Chat Tab
            with gr.Tab("💬 Chat"):
                with gr.Row():
                    with gr.Column(scale=5):
                        prompt_input = gr.Textbox(
                            label="Programming Request",
                            placeholder="e.g. Write an is_palindrome function to check if a string is a palindrome.",
                            lines=3,
                        )
                        run_btn = gr.Button("Generate Code & Run Sandbox", variant="primary")
                        active_thread_id = gr.Textbox(label="Thread ID", visible=True, interactive=False)

                        with gr.Row():
                            thumb_up_btn = gr.Button("👍 Pass Feedback")
                            thumb_down_btn = gr.Button("👎 Flaw / Bug Feedback")
                        feedback_comment = gr.Textbox(label="Feedback Comment", placeholder="Describe issues or expected output...")
                        feedback_status = gr.Markdown()

                    with gr.Column(scale=7):
                        report_out = gr.Markdown(label="Run Report")
                        with gr.Accordion("Generated Solution Code", open=True):
                            code_out = gr.Code(language="python", label="solution.py")
                        with gr.Accordion("Generated Sandbox Tests", open=False):
                            tests_out = gr.Code(language="python", label="test_solution.py")

                run_btn.click(
                    submit_request,
                    inputs=[prompt_input],
                    outputs=[report_out, code_out, tests_out, active_thread_id],
                )
                thumb_up_btn.click(
                    lambda tid, c: submit_feedback(tid, "up", c),
                    inputs=[active_thread_id, feedback_comment],
                    outputs=[feedback_status],
                )
                thumb_down_btn.click(
                    lambda tid, c: submit_feedback(tid, "down", c),
                    inputs=[active_thread_id, feedback_comment],
                    outputs=[feedback_status],
                )

            # 2. My Runs Tab
            with gr.Tab("📜 My Runs"):
                gr.Markdown("Runs scoped exclusively to your active identity (`RunStore.get_my_runs()`).")
                refresh_runs_btn = gr.Button("Refresh Runs")
                runs_table = gr.Dataframe(
                    headers=["Run ID", "Owner", "Request", "Passed", "Failed", "Exit Code", "Created At"],
                    value=get_my_runs_table,
                )
                refresh_runs_btn.click(get_my_runs_table, outputs=[runs_table])

            # 3. Reviewer Queue Tab
            with gr.Tab("🛡️ Reviewer Queue", visible=initial_ident.role == "instructor") as reviewer_tab:
                gr.Markdown("Pending risky code approvals (restricted to Instructor/Approver role).")
                refresh_appr_btn = gr.Button("Refresh Pending Approvals")
                approvals_table = gr.Dataframe(
                    headers=["Thread ID", "Requester", "Policy Reasons", "Code Hash", "Created At"],
                    value=get_pending_approvals,
                )
                refresh_appr_btn.click(get_pending_approvals, outputs=[approvals_table])

                with gr.Row():
                    rev_thread_id = gr.Textbox(label="Thread ID to Approve/Reject")
                    rev_comment = gr.Textbox(label="Reviewer Comment")
                with gr.Row():
                    approve_btn = gr.Button("Approve & Execute", variant="primary")
                    reject_btn = gr.Button("Reject & Stop", variant="stop")
                review_status = gr.Markdown()

                approve_btn.click(
                    lambda tid, c: process_review_decision(tid, "Approve", c),
                    inputs=[rev_thread_id, rev_comment],
                    outputs=[review_status],
                )
                reject_btn.click(
                    lambda tid, c: process_review_decision(tid, "Reject", c),
                    inputs=[rev_thread_id, rev_comment],
                    outputs=[review_status],
                )

            # 4. Support Tickets Tab
            with gr.Tab("🎫 Tickets"):
                gr.Markdown("Handoff tickets escalated to human assistance (strictly masked inputs).")
                refresh_tkt_btn = gr.Button("Refresh Tickets")
                tickets_table = gr.Dataframe(
                    headers=["Ticket ID", "Priority", "SLA (hrs)", "Masked Request", "Reason", "Created At"],
                    value=get_tickets_table,
                )
                refresh_tkt_btn.click(get_tickets_table, outputs=[tickets_table])

            # 5. Ops & Resilience Tab
            with gr.Tab("⚙️ Ops & Telemetry"):
                ops_summary = gr.Markdown(value=get_ops_metrics)
                chaos_slider = gr.Slider(0, 1, value=0, step=0.1, label="Chaos: fraction of model calls to fail (instructor only)")
                chaos_status = gr.Markdown()
                chaos_slider.release(set_chaos, inputs=[chaos_slider], outputs=[chaos_status])
                refresh_ops_btn = gr.Button("Refresh Metrics")
                refresh_ops_btn.click(get_ops_metrics, outputs=[ops_summary])

        profile_dropdown.change(
            update_profile, inputs=[profile_dropdown], outputs=[profile_badge, reviewer_tab]
        )

    return demo


def main() -> None:
    app = build_app()
    # Loopback by default: the demo profile switch is process-global, so do not expose it publicly.
    app.launch(server_name=os.environ.get("CODEFORGE_HOST", "127.0.0.1"), server_port=7860, share=False)


if __name__ == "__main__":
    main()
