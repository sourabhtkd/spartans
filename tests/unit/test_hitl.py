from codeforge.config import Identity
from codeforge.hitl.approval import human_approval_node
from codeforge.hitl.handoff import create_ticket, should_escalate
from codeforge.privacy.pii import mask_text
from codeforge.state import ApprovalDecision, CodeForgeState, PolicyDecision
from codeforge.store.db import init_db


def test_should_escalate_rules():
    # Normal request
    esc, _, _ = should_escalate("Write a binary search function")
    assert not esc

    # Explicit human request (standard -> P2)
    esc, reason, prio = should_escalate("I want to speak to a human please")
    assert esc is True
    assert prio == "P2"
    assert "human assistance" in reason

    # Urgent explicit human request (all-caps / !! -> P1)
    esc, reason, prio = should_escalate("HELP ME GET A HUMAN RIGHT NOW!!")
    assert esc is True
    assert prio == "P1"

    # Repeated policy denials
    esc, _, prio = should_escalate("simple query", policy_denials=2)
    assert esc is True
    assert prio == "P2"

    # Repeated repair attempts
    esc, _, prio = should_escalate("simple query", repair_attempts=2)
    assert esc is True
    assert prio == "P2"


def test_student_c_ticket_created_strictly_masked():
    test_db = init_db("sqlite:///:memory:")
    raw_prompt = "I need a human agent!! My API key is sk-1234567890abcdef1234 and phone 9876543210"
    masked_prompt = mask_text(raw_prompt)

    state: CodeForgeState = {
        "request_masked": masked_prompt,
        "attempts": 2,
    }
    esc, reason, prio = should_escalate(masked_prompt)
    assert esc is True
    assert prio == "P1"

    ticket = create_ticket(state, session_factory=test_db, priority=prio, reason=reason)
    assert ticket.priority == "P1"
    assert "sk-" not in ticket.request_masked
    assert "9876543210" not in ticket.request_masked
    assert "[SECRET]" in ticket.request_masked
    assert "[PHONE]" in ticket.request_masked


def test_approval_node_blocks_student_role(monkeypatch):
    # Mock interrupt to simulate a student attempting to approve
    monkeypatch.setattr(
        "codeforge.hitl.approval.interrupt",
        lambda payload: {"approved": True, "approver_id": "u_bob", "comment": "Approved by Bob"},
    )
    state: CodeForgeState = {
        "thread_id": "th_1",
        "code": "import requests",
        "tests": "def test(): pass",
        "policy": PolicyDecision(verdict="needs_approval", reasons=["requests"]),
        "identity": Identity(user_id="u_alice", role="student"),
    }
    result = human_approval_node(state)
    decision = result["approval"]
    assert isinstance(decision, ApprovalDecision)
    assert decision.approved is False
    assert "only instructors may approve" in decision.comment


def test_approval_node_blocks_self_approval(monkeypatch):
    # Mock interrupt to simulate the requester trying to approve their own request
    monkeypatch.setattr(
        "codeforge.hitl.approval.interrupt",
        lambda payload: {"approved": True, "approver_id": "u_prof", "comment": "I approve myself"},
    )
    state: CodeForgeState = {
        "thread_id": "th_prof",
        "code": "import requests",
        "tests": "def test(): pass",
        "policy": PolicyDecision(verdict="needs_approval", reasons=["requests"]),
        "identity": Identity(user_id="u_prof", role="instructor"),
    }
    result = human_approval_node(state)
    decision = result["approval"]
    assert decision.approved is False
    assert "self-approval is forbidden" in decision.comment


def test_human_request_regex_is_not_too_broad():
    assert not should_escalate("Write a Python agent class")[0]
    assert not should_escalate("Write a person class with name and age")[0]
    assert not should_escalate("simulate a human readable size formatter")[0]
    assert should_escalate("THIS IS NOT WORKING!!! get me a human.")[0]
    assert should_escalate("I want to talk to a real person")[0]
    assert should_escalate("human please")[0]


def test_repeated_policy_denials_escalate_across_requests():
    from codeforge.config import Identity
    from codeforge.store.runs import RunStore

    store = RunStore(identity_provider=lambda: Identity(user_id="u_x", role="student"))
    assert store.record_policy_denial() == 1
    assert not should_escalate("calm", policy_denials=1)[0]
    assert store.record_policy_denial() == 2
    assert should_escalate("calm", policy_denials=2)[0]
