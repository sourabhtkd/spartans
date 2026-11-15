"""The Gradio page: tabs, widgets and which handler each one calls (logic lives in handlers.py)."""
import os
from typing import Any

import gradio as gr

from codeforge.config import current_identity, get_settings
from codeforge.ui import handlers


def build_app() -> gr.Blocks:
    """Lay out the five tabs and connect each widget to a handler."""
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
            profile_badge = gr.Markdown(handlers.identity_badge())

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
                    handlers.submit_request,
                    inputs=[prompt_input],
                    outputs=[report_out, code_out, tests_out, active_thread_id],
                )
                thumb_up_btn.click(
                    lambda tid, c: handlers.record_feedback(tid, "up", c),
                    inputs=[active_thread_id, feedback_comment],
                    outputs=[feedback_status],
                )
                thumb_down_btn.click(
                    lambda tid, c: handlers.record_feedback(tid, "down", c),
                    inputs=[active_thread_id, feedback_comment],
                    outputs=[feedback_status],
                )

            # 2. My Runs Tab
            with gr.Tab("📜 My Runs"):
                gr.Markdown("Runs scoped exclusively to your active identity (`RunStore.get_my_runs()`).")
                refresh_runs_btn = gr.Button("Refresh Runs")
                runs_table = gr.Dataframe(
                    headers=["Run ID", "Owner", "Request", "Passed", "Failed", "Exit Code", "Created At"],
                    value=handlers.my_runs_table,
                )
                refresh_runs_btn.click(handlers.my_runs_table, outputs=[runs_table])

            # 3. Reviewer Queue Tab
            with gr.Tab("🛡️ Reviewer Queue", visible=initial_ident.role == "instructor") as reviewer_tab:
                gr.Markdown("Pending risky code approvals (restricted to Instructor/Approver role).")
                refresh_appr_btn = gr.Button("Refresh Pending Approvals")
                approvals_table = gr.Dataframe(
                    headers=["Thread ID", "Requester", "Policy Reasons", "Code Hash", "Created At"],
                    value=handlers.pending_approvals_table,
                )
                refresh_appr_btn.click(handlers.pending_approvals_table, outputs=[approvals_table])

                with gr.Row():
                    rev_thread_id = gr.Textbox(label="Thread ID to Approve/Reject")
                    rev_comment = gr.Textbox(label="Reviewer Comment")
                with gr.Row():
                    approve_btn = gr.Button("Approve & Execute", variant="primary")
                    reject_btn = gr.Button("Reject & Stop", variant="stop")
                review_status = gr.Markdown()

                approve_btn.click(
                    lambda tid, c: handlers.review_decision(tid, "Approve", c),
                    inputs=[rev_thread_id, rev_comment],
                    outputs=[review_status],
                )
                reject_btn.click(
                    lambda tid, c: handlers.review_decision(tid, "Reject", c),
                    inputs=[rev_thread_id, rev_comment],
                    outputs=[review_status],
                )

            # 4. Support Tickets Tab
            with gr.Tab("🎫 Tickets"):
                gr.Markdown("Handoff tickets escalated to human assistance (strictly masked inputs).")
                refresh_tkt_btn = gr.Button("Refresh Tickets")
                tickets_table = gr.Dataframe(
                    headers=["Ticket ID", "Priority", "SLA (hrs)", "Masked Request", "Reason", "Created At"],
                    value=handlers.tickets_table,
                )
                refresh_tkt_btn.click(handlers.tickets_table, outputs=[tickets_table])

            # 5. Ops & Resilience Tab
            with gr.Tab("⚙️ Ops & Telemetry"):
                ops_summary_view = gr.Markdown(value=handlers.ops_summary)
                chaos_slider = gr.Slider(0, 1, value=0, step=0.1, label="Chaos: fraction of model calls to fail (instructor only)")
                chaos_status = gr.Markdown()
                chaos_slider.release(handlers.set_chaos_rate, inputs=[chaos_slider], outputs=[chaos_status])
                refresh_ops_btn = gr.Button("Refresh Metrics")
                refresh_ops_btn.click(handlers.ops_summary, outputs=[ops_summary_view])

        def on_profile_change(profile_name: str) -> tuple[str, Any]:
            badge, show_reviewer_tab = handlers.switch_profile(profile_name)
            return badge, gr.update(visible=show_reviewer_tab)

        profile_dropdown.change(on_profile_change, inputs=[profile_dropdown], outputs=[profile_badge, reviewer_tab])

    return demo


def main() -> None:
    app = build_app()
    # Loopback by default: the demo profile switch is process-global, so do not expose it publicly.
    app.launch(server_name=os.environ.get("CODEFORGE_HOST", "127.0.0.1"), server_port=7860, share=False)


if __name__ == "__main__":
    main()
