from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from codeforge.config import get_settings
from codeforge.state import CodeForgeState

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_ENV = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=False)


def determine_status_message(state: CodeForgeState) -> str:
    """Computes honest status message in pure Python from real execution exit codes.
    Never lets LLM claim tests passed when exit_code != 0.
    """
    if state.get("served_tier") == "refused":
        return "❌ CodeForge cannot generate code right now. Handoff ticket created."

    ticket = state.get("ticket")
    if ticket and not state.get("code") and ticket.reason.startswith("User requested human"):
        return (
            f"🙋 Your request was escalated to a human (ticket {ticket.ticket_id}, "
            f"{ticket.priority}, SLA {ticket.sla_hours}h). Nothing was executed."
        )

    policy = state.get("policy")
    if policy and policy.verdict == "deny":
        reasons_str = "; ".join(policy.reasons) if policy.reasons else "Restricted operation"
        return f"❌ Refused by policy: {reasons_str}. Nothing was executed."

    approval = state.get("approval")
    if approval and not approval.approved:
        comment_str = approval.comment or "Approval denied"
        return f"Reviewer rejected: {comment_str}. Nothing was executed."

    route = state.get("route")
    if route and route.kind != "python_function":
        return f"ℹ️ Request routed to '{route.kind}': {route.language} execution is currently unsupported/not enabled."

    run_result = state.get("run_result")
    if run_result is None and state.get("tests_valid") is False:
        return "⚠️ The generated tests were invalid, so nothing was executed. A handoff ticket was created."
    if run_result is None:
        return "ℹ️ Execution not run."

    if run_result.timed_out:
        timeout_s = get_settings().sandbox.get("timeout_s", 10)
        return f"⏱ Tests did not finish within {timeout_s}s and were killed."

    if run_result.exit_code is None:
        return f"ℹ️ No test result is available yet: {run_result.stdout or 'run did not complete'}"

    if run_result.exit_code == 0 and not run_result.timed_out:
        p = run_result.passed
        return f"✅ Tests passed ({p}/{p})"

    first_fail = run_result.last_failing_test or "assertion error"
    return f"❌ Tests failed ({run_result.failed} failing; first: {first_fail})"


def render_report(state: CodeForgeState) -> str:
    """Renders final Markdown report for the run using Jinja2."""
    template = _ENV.get_template("report.md.j2")
    status_msg = determine_status_message(state)
    run_result = state.get("run_result")
    ticket = state.get("ticket")

    return template.render(
        status_message=status_msg,
        code=state.get("code"),
        tests=state.get("tests"),
        served_tier=state.get("served_tier", "primary"),
        cache_hit=state.get("cache_hit", False),
        is_cached_run=run_result.cached if run_result else False,
        ticket_id=ticket.ticket_id if ticket else None,
        ticket_reason=ticket.reason if ticket else None,
        run_result=run_result,
        stdout=run_result.stdout if run_result else "",
    )
