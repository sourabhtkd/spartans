"""Human-in-the-loop approval for risky (needs_approval) code.

Security properties:
  * The approver's identity is taken from server-side config in ``resume_approval``,
    never from a caller-supplied payload.
  * Only instructors may approve; self-approval is refused.
  * Pending approvals expire (fail closed) via ``expire_stale``.
  * Every request gets its own audit row; decisions never overwrite another request's row.
"""
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.types import Command, interrupt

from codeforge.config import current_identity, get_settings
from codeforge.observability import (
    current_request_id,
    flush_langfuse,
    get_logger,
    langfuse_run_config,
    run_facts,
    trace_turn,
)
from codeforge.state import ApprovalDecision, CodeForgeState, PolicyDecision
from codeforge.store.db import Approval, init_db

logger = get_logger("approval")

SYSTEM_EXPIRY_ID = "system:expiry"


def _get_or_create_pending(thread_id: str, requester_id: str, code_hash: str, reasons: str) -> str:
    """Return the id of this thread's pending approval row, creating it if needed.

    LangGraph re-executes the node from the top when resuming after interrupt(), so this
    must be idempotent: a resume reuses the pending row rather than creating a duplicate.
    """
    sf = init_db()
    with sf() as session:
        rec = (
            session.query(Approval)
            .filter(Approval.thread_id == thread_id, Approval.verdict == "pending")
            .first()
        )
        if rec is None:
            rec = Approval(
                approval_id=f"appr_{thread_id}_{uuid.uuid4().hex[:6]}",
                thread_id=thread_id,
                requester_id=requester_id,
                code_hash=code_hash,
                reasons=reasons,
                verdict="pending",
                comment="",
            )
            session.add(rec)
            session.commit()
        return str(rec.approval_id)


def _parse_decision(raw: Any) -> ApprovalDecision:
    """Accept the resume payload as a dict or an ApprovalDecision; anything else is a rejection."""
    if isinstance(raw, ApprovalDecision):
        return raw
    if isinstance(raw, dict):
        return ApprovalDecision(**raw)
    return ApprovalDecision(approved=False, approver_id="", comment="Invalid decision payload")


def _approver_role(approver_id: str) -> str:
    for profile in get_settings().profiles.values():
        if profile.user_id == approver_id:
            return profile.role
    return "student"  # unknown people get no privileges


def _enforce_approver_rules(decision: ApprovalDecision, requester_id: str) -> ApprovalDecision:
    """Turn an approval into a rejection unless an instructor other than the requester gave it.

    resume_approval already refuses these cases up front; this is the second line of defence.
    Rejections always pass through unchanged.
    """
    if not decision.approved:
        return decision
    if _approver_role(decision.approver_id) != "instructor":
        reason = "Rejected: only instructors may approve code."
    elif decision.approver_id == requester_id:
        reason = "Rejected: self-approval is forbidden."
    else:
        return decision
    return ApprovalDecision(approved=False, approver_id=decision.approver_id, comment=reason)


def human_approval_node(state: CodeForgeState) -> dict[str, Any]:
    """Pause the graph via interrupt() until an instructor decides. Fails closed."""
    code = state.get("code", "")
    tests = state.get("tests", "")
    policy = state.get("policy", PolicyDecision(verdict="needs_approval", reasons=[]))
    identity = state.get("identity") or current_identity()
    requester_id = identity.user_id
    thread_id = state.get("thread_id", "default")
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]

    approval_id = _get_or_create_pending(thread_id, requester_id, code_hash, "; ".join(policy.reasons))

    raw_decision = interrupt(
        {
            "thread_id": thread_id,
            "code": code,
            "tests": tests,
            "reasons": policy.reasons,
            "requester": requester_id,
            "idem_key": state.get("idempotency_key", ""),
        }
    )

    decision = _enforce_approver_rules(_parse_decision(raw_decision), requester_id)

    with init_db()() as session:
        rec = session.get(Approval, approval_id)
        if rec is not None:
            rec.approver_id = decision.approver_id
            rec.verdict = "approved" if decision.approved else "rejected"
            rec.comment = decision.comment
            session.commit()
    logger.info(
        "approval_decided", thread_id=thread_id, verdict="approved" if decision.approved else "rejected"
    )
    return {"approval": decision}


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _traced_resume(graph: Any, thread_id: str, payload: dict[str, Any], *, user_id: str, role: str) -> Any:
    """Resume the paused graph inside its own Langfuse trace (session = the run's thread)."""
    current_request_id.set(thread_id)  # metering rows join the original request
    with trace_turn(
        session_id=thread_id,
        user_id=user_id,
        name="approval_resume",
        tags=[role, "codeforge", "resume"],
        input={"thread_id": thread_id, "approved": payload.get("approved"), "comment": payload.get("comment")},
    ) as turn:
        config = _config(thread_id)
        config["metadata"] = {"langfuse_session_id": thread_id, "langfuse_user_id": user_id, "thread_id": thread_id}
        result = graph.invoke(Command(resume=payload), config=langfuse_run_config(config, turn))
        snap = graph.get_state(_config(thread_id))
        report = snap.values.get("report")
        turn.output = report if isinstance(report, str) else None
        turn.metadata = run_facts(snap.values, snap.next)
    flush_langfuse()
    return result


def resume_approval(graph: Any, thread_id: str, approved: bool, comment: str = "") -> Any:
    """Resume a paused run. The approver is the *server-side* current identity.

    Raises PermissionError for non-instructors and for self-approval (the run stays pending
    so a different instructor can decide), and ValueError if nothing is pending.
    """
    approver = current_identity()
    if approver.role != "instructor":
        raise PermissionError("Only instructors may approve or reject pending code.")

    snap = graph.get_state(_config(thread_id))
    if "human_approval" not in tuple(snap.next):
        raise ValueError(f"No pending approval for thread '{thread_id}'.")
    requester = snap.values.get("identity")
    if approved and requester is not None and requester.user_id == approver.user_id:
        raise PermissionError("Self-approval is forbidden; another instructor must approve.")

    decision = ApprovalDecision(
        approved=approved,
        approver_id=approver.user_id,
        comment=comment or ("Approved by instructor" if approved else "Rejected by instructor"),
    )
    return _traced_resume(
        graph, thread_id, decision.model_dump(), user_id=approver.user_id, role=approver.role
    )


def list_pending() -> list[Approval]:
    with init_db()() as session:
        return list(session.query(Approval).filter(Approval.verdict == "pending").all())


def expire_stale(graph: Any, now: datetime | None = None, expiry_minutes: float | None = None) -> list[str]:
    """Reject (fail closed) every approval pending longer than the configured expiry.

    Returns the thread ids that were expired.
    """
    minutes = (
        expiry_minutes
        if expiry_minutes is not None
        else get_settings().approval.expiry_minutes
    )
    now_naive = (now or datetime.now(UTC)).replace(tzinfo=None)
    cutoff = now_naive - timedelta(minutes=minutes)
    expired: list[str] = []
    for rec in list_pending():
        created = rec.created_at.replace(tzinfo=None) if rec.created_at else now_naive
        if created >= cutoff:
            continue
        payload = ApprovalDecision(
            approved=False, approver_id=SYSTEM_EXPIRY_ID, comment="Rejected: approval request expired."
        ).model_dump()
        try:
            _traced_resume(graph, rec.thread_id, payload, user_id=SYSTEM_EXPIRY_ID, role="system")
        except Exception as exc:  # noqa: BLE001 - one bad thread must not block the rest
            logger.warning("expire_failed", thread_id=rec.thread_id, error=str(exc))
            with init_db()() as session:
                row = session.get(Approval, rec.approval_id)
                if row is not None:
                    row.verdict = "rejected"
                    row.approver_id = SYSTEM_EXPIRY_ID
                    row.comment = "Rejected: approval request expired."
                    session.commit()
        expired.append(rec.thread_id)
    return expired
