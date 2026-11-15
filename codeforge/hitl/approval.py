import hashlib
from datetime import UTC, datetime
from typing import Any

from langgraph.types import Command, interrupt

from codeforge.config import current_identity, get_settings
from codeforge.state import ApprovalDecision, CodeForgeState, PolicyDecision
from codeforge.store.db import Approval, init_db


def human_approval_node(state: CodeForgeState) -> dict[str, Any]:
    """Human-in-the-loop approval node.
    Pauses graph execution via interrupt() until an instructor provides an approval decision.
    Validates approver identity and blocks self-approval.
    """
    code = state.get("code", "")
    tests = state.get("tests", "")
    policy = state.get("policy", PolicyDecision(verdict="needs_approval", reasons=[]))
    identity = state.get("identity", current_identity())
    requester_id = identity.user_id
    idem_key = state.get("idempotency_key", "")
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]

    # Save pending record in DB
    session_factory = init_db()
    with session_factory() as session:
        thread_id = state.get("thread_id", "default")
        pending_rec = Approval(
            approval_id=f"appr_{thread_id}",
            thread_id=thread_id,
            requester_id=requester_id,
            code_hash=code_hash,
            reasons="; ".join(policy.reasons),
            verdict="pending",
            comment="",
        )
        session.merge(pending_rec)
        session.commit()

    # Pause execution until resumed
    raw_decision = interrupt({
        "thread_id": state.get("thread_id", ""),
        "code": code,
        "tests": tests,
        "reasons": policy.reasons,
        "requester": requester_id,
        "idem_key": idem_key,
    })

    # Validate decision
    if isinstance(raw_decision, dict):
        decision = ApprovalDecision(**raw_decision)
    elif isinstance(raw_decision, ApprovalDecision):
        decision = raw_decision
    else:
        decision = ApprovalDecision(approved=False, approver_id="", comment="Invalid decision payload")

    # Look up approver role from settings profiles
    settings = get_settings()
    approver_role = "student"
    for prof in settings.profiles.values():
        if prof.user_id == decision.approver_id:
            approver_role = prof.role
            break

    # Security rule 1: Only instructors may approve
    if approver_role != "instructor":
        decision = ApprovalDecision(
            approved=False,
            approver_id=decision.approver_id,
            comment="Rejected: only instructors may approve code.",
        )
    # Security rule 2: Self-approval is forbidden
    elif decision.approver_id == requester_id:
        decision = ApprovalDecision(
            approved=False,
            approver_id=decision.approver_id,
            comment="Rejected: self-approval is forbidden.",
        )

    # Update DB record with final verdict
    with session_factory() as session:
        rec = session.query(Approval).filter(Approval.thread_id == state.get("thread_id", "default")).first()
        if rec:
            rec.approver_id = decision.approver_id
            rec.verdict = "approved" if decision.approved else "rejected"
            rec.comment = decision.comment
            rec.updated_at = datetime.now(UTC)
            session.commit()

    return {"approval": decision}


def resume_approval(graph: Any, thread_id: str, decision: ApprovalDecision) -> Any:
    """Resumes an interrupted graph run with the reviewer's approval decision."""
    return graph.invoke(
        Command(resume=decision.model_dump()),
        config={"configurable": {"thread_id": thread_id}},
    )
