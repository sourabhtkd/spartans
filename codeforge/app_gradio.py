import json
import os
import uuid

import gradio as gr

from codeforge.config import clear_settings_cache, current_identity, get_settings
from codeforge.graph import build_graph
from codeforge.hitl.approval import resume_approval
from codeforge.state import ApprovalDecision
from codeforge.store.db import Approval, Feedback, Metric, Ticket, init_db
from codeforge.store.runs import RunStore


def submit_request(prompt: str, thread_id: str | None = None) -> tuple[str, str, str, str]:
    if not prompt or not prompt.strip():
        return "Please enter a valid programming request.", "", "", ""

    tid = thread_id or f"run_{uuid.uuid4().hex[:8]}"
    ident = current_identity()
    graph = build_graph()
    config = {"configurable": {"thread_id": tid}}

    initial_state = {
        "thread_id": tid,
        "raw_request_masked": prompt,
        "identity": ident,
    }

    # Stream graph execution
    for _ in graph.stream(initial_state, config=config):
        pass

    state = graph.get_state(config)

    # Check for interrupt
    if state.next and "human_approval" in state.next:
        policy = state.values.get("policy")
        reasons = "; ".join(policy.reasons) if policy else "File/Network access requested"
        msg = (
            f"### ⏸ Execution Paused: Reviewer Approval Required\n"
            f"- **Thread ID**: `{tid}`\n"
            f"- **Policy Reasons**: {reasons}\n"
            f"Please switch to the **Reviewer Queue** tab as an instructor to Approve or Reject this run."
        )
        return msg, state.values.get("code", ""), state.values.get("tests", ""), tid

    report = state.values.get("report", "No report generated.")
    code = state.values.get("code", "")
    tests = state.values.get("tests", "")
    return report, code, tests, tid


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
    with sf() as session:
        approvals = session.query(Approval).filter(Approval.verdict == "pending").all()
        return [
            [a.thread_id, a.requester_id, a.reasons[:50], a.code_hash, str(a.created_at)[:19]]
            for a in approvals
        ]


def process_review_decision(thread_id: str, action: str, comment: str) -> str:
    ident = current_identity()
    approved = (action == "Approve")
    decision = ApprovalDecision(
        approved=approved,
        approver_id=ident.user_id,
        comment=comment or ("Approved by instructor" if approved else "Rejected by instructor"),
    )
    graph = build_graph()
    try:
        resume_approval(graph, thread_id, decision)
        return f"Decision '{action}' applied to thread {thread_id}."
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
    sf = init_db()
    with sf() as session:
        metrics = session.query(Metric).order_by(Metric.created_at.desc()).limit(20).all()
        total_tokens = sum(m.input_tokens + m.output_tokens for m in metrics)
        total_cost = sum(m.cost_usd for m in metrics)

    cost_summary = (
        f"### Ops Telemetry Summary\n"
        f"- **Recent Tracked Calls**: {len(metrics)}\n"
        f"- **Recent Total Tokens**: {total_tokens}\n"
        f"- **Estimated USD Cost**: ${total_cost:.5f}\n"
    )
    return cost_summary


def update_profile(profile_name: str) -> str:
    os.environ["CODEFORGE_PROFILE"] = profile_name
    clear_settings_cache()
    ident = current_identity()
    return f"Active Identity: [{ident.user_id}] | Role: {ident.role}"


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
            profile_dropdown.change(update_profile, inputs=[profile_dropdown], outputs=[profile_badge])

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
            with gr.Tab("🛡️ Reviewer Queue"):
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
                refresh_ops_btn = gr.Button("Refresh Metrics")
                refresh_ops_btn.click(get_ops_metrics, outputs=[ops_summary])

    return demo


def main() -> None:
    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=7860, share=False)


if __name__ == "__main__":
    main()
