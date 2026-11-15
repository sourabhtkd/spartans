"""Humans in the loop: approval pause/resume rules and escalation to a person."""
from typing import Any

import pytest

from codeforge.config import Identity
from codeforge.entrypoint import submit
from codeforge.hitl.approval import expire_stale, human_approval_node, resume_approval
from codeforge.hitl.handoff import create_ticket, should_escalate
from codeforge.privacy.pii import mask_text
from codeforge.store.db import init_db
from evals.checks.common import Env, risky_graph
from tests.helpers import (
    memory_graph,
    patch_llm,
    sandbox_spy,
    scripted_fake,
)


def check_approval_pause(case: dict, env: Env) -> None:
    approver = "u_bob" if case["expect"] == "rejected_role" else "u_prof"
    requester = Identity(user_id="u_alice", role="student") if approver == "u_bob" else Identity(user_id="u_prof", role="instructor")
    env.mp.setattr("codeforge.hitl.approval.interrupt", lambda payload: {"approved": True, "approver_id": approver})
    res = human_approval_node({"thread_id": f"th_{case['id']}", "code": "import requests", "identity": requester})
    assert res["approval"].approved is False

def check_student_cannot_resume(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    graph = risky_graph(env)
    assert submit("Write w", thread_id="g1", graph=graph).pending_approval
    with pytest.raises(PermissionError):
        resume_approval(graph, "g1", True)
    assert calls == []

def check_instructor_resume(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    graph = risky_graph(env)
    submit("Write w", thread_id="g2", graph=graph)
    assert calls == []  # nothing executes before the approver resumes
    env.as_profile("instructor")
    resume_approval(graph, "g2", True, "ok")
    assert len(calls) == 1

def check_approval_expiry(case: dict, env: Env) -> None:
    from datetime import UTC, datetime, timedelta

    calls = sandbox_spy(env.mp)
    graph = risky_graph(env)
    submit("Write w", thread_id="g3", graph=graph)
    assert expire_stale(graph, now=datetime.now(UTC) + timedelta(minutes=45)) == ["g3"]
    assert calls == []
    assert graph.get_state({"configurable": {"thread_id": "g3"}}).values["approval"].approved is False

def check_escalation(case: dict, env: Env) -> None:
    if case["expect"] == "P1":
        esc, _, prio = should_escalate(case["input"])
        assert esc and prio == "P1"
    else:  # ticket_masked
        tkt = create_ticket({"request_masked": mask_text(case["input"])}, init_db("sqlite:///:memory:"))
        assert "sk-" not in tkt.request_masked and "[SECRET]" in tkt.request_masked

def check_escalation_negative(case: dict, env: Env) -> None:
    assert should_escalate(case["input"])[0] is False

def check_escalation_positive(case: dict, env: Env) -> None:
    assert should_escalate(case["input"])[0] is True

def check_student_c_graph(case: dict, env: Env) -> None:
    calls = sandbox_spy(env.mp)
    res = submit(case["input"], graph=memory_graph())
    t = res.values["ticket"]
    assert t.priority == "P1" and "sk-" not in t.request_masked and "[SECRET]" in t.request_masked
    assert calls == []

def check_repeated_denial(case: dict, env: Env) -> None:
    patch_llm(env.mp, scripted_fake("import os\n\ndef f():\n    os.remove('x')\n"))
    graph = memory_graph()
    assert submit(case["input"], thread_id="d1", graph=graph).values.get("ticket") is None
    second = submit(case["input"] + " now", thread_id="d2", graph=graph)
    assert second.values["ticket"] is not None


CHECKS: dict[str, Any] = {
    "approval_pause": check_approval_pause,
    "student_cannot_resume": check_student_cannot_resume,
    "instructor_resume": check_instructor_resume,
    "approval_expiry": check_approval_expiry,
    "escalation": check_escalation,
    "escalation_negative": check_escalation_negative,
    "escalation_positive": check_escalation_positive,
    "student_c_graph": check_student_c_graph,
    "repeated_denial": check_repeated_denial,
}
