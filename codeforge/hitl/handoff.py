import re
import uuid
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from codeforge.state import CodeForgeState, HandoffTicket
from codeforge.store.db import Ticket, init_db

# Matches explicit requests for a person ("get me a human", "talk to a real person",
# "human please"). A bare "agent"/"person" (e.g. "write an agent class") must NOT match.
HUMAN_REQUEST_REGEX = re.compile(
    r"\b(?:get|give|need|want|talk to|speak (?:to|with)|connect me (?:to|with)|escalate to)\s+"
    r"(?:me\s+)?(?:an?\s+)?(?:real\s+|live\s+)?"
    r"(?:human(?:\s+(?:agent|being|support))?|person|someone real|support agent|live agent)\b"
    r"|\bhuman please\b",
    re.IGNORECASE,
)


def should_escalate(
    request_text: str,
    policy_denials: int = 0,
    repair_attempts: int = 0,
) -> tuple[bool, str, Literal["P1", "P2", "P3"]]:
    """Plain-Python business logic to decide whether to escalate to a human ticket.
    Returns (should_escalate, reason, priority).
    """
    if HUMAN_REQUEST_REGEX.search(request_text):
        # P1 if all-caps, contains multiple exclamation marks, or says URGENT
        is_urgent = (
            request_text.isupper()
            or "!!" in request_text
            or "URGENT" in request_text.upper()
        )
        priority: Literal["P1", "P2", "P3"] = "P1" if is_urgent else "P2"
        return True, "User requested human assistance", priority

    if policy_denials >= 2:
        return True, "Repeated policy denials in session", "P2"

    if repair_attempts >= 2:
        return True, "Repeated failed repair attempts in sandbox", "P2"

    return False, "", "P3"


def create_ticket(
    state: CodeForgeState,
    session_factory: sessionmaker[Session] | None = None,
    priority: Literal["P1", "P2", "P3"] = "P2",
    reason: str = "Escalated for human assistance",
) -> HandoffTicket:
    """Creates and persists a HandoffTicket strictly using masked state fields."""
    ticket_id = f"tkt_{uuid.uuid4().hex[:8]}"
    sla_map = {"P1": 4, "P2": 24, "P3": 72}
    sla_hours = sla_map.get(priority, 24)

    run_res = state.get("run_result")
    last_failing = run_res.last_failing_test if run_res else None

    # Masked request only — prevents secret/Aadhaar leakage
    req_masked = state.get("request_masked", "")
    attempts = state.get("attempts", 0)

    ticket = HandoffTicket(
        ticket_id=ticket_id,
        priority=priority,
        sla_hours=sla_hours,
        request_masked=req_masked,
        attempts=attempts,
        last_failing_test=last_failing,
        reason=reason,
    )

    sf = session_factory or init_db()
    with sf() as session:
        row = Ticket(
            ticket_id=ticket.ticket_id,
            priority=ticket.priority,
            sla_hours=ticket.sla_hours,
            request_masked=ticket.request_masked,
            attempts=ticket.attempts,
            last_failing_test=ticket.last_failing_test,
            reason=ticket.reason,
        )
        session.add(row)
        session.commit()

    return ticket
