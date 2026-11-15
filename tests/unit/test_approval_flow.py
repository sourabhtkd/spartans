import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from codeforge.config import Identity
from codeforge.entrypoint import submit
from codeforge.hitl.approval import expire_stale, human_approval_node, resume_approval
from codeforge.pipeline import build_graph
from codeforge.state import GeneratedCode, GeneratedTests, Requirements, RouteDecision
from codeforge.store.db import Approval, init_db
from tests.fakes import FakeLLM

RISKY = "import tempfile\n\ndef w():\n    with open('x.txt', 'w') as f:\n        f.write('hi')\n    return 1\n"
TESTS = "from solution import w\n\ndef test_w():\n    assert w() == 1\n    assert w() == 1\n    assert w() == 1\n"


@pytest.fixture
def graph(monkeypatch):
    fake = FakeLLM()
    fake.set_response("router", RouteDecision(kind="python_function"))
    fake.set_response("requirements", Requirements(function_name="w"))
    fake.set_response("codegen", GeneratedCode(code=RISKY))
    fake.set_response("testgen", GeneratedTests(tests=TESTS))
    for a in ("router", "requirements", "codegen", "testgen"):
        monkeypatch.setattr(f"codeforge.agents.{a}.resilient_invoke", fake)
    calls = []
    import codeforge.pipeline.nodes as g
    real = g.run_pytest_sandboxed
    monkeypatch.setattr(g, "run_pytest_sandboxed", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    gr = build_graph(checkpointer=SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    gr.calls = calls
    return gr


def test_student_cannot_resume_and_nothing_runs(graph):
    res = submit("Write w", thread_id="t1", graph=graph)
    assert res.pending_approval
    with pytest.raises(PermissionError):
        resume_approval(graph, "t1", True)
    assert graph.calls == []
    assert "human_approval" in graph.get_state({"configurable": {"thread_id": "t1"}}).next


def test_instructor_approves_runs_once(graph, as_profile):
    submit("Write w", thread_id="t2", graph=graph)
    assert graph.calls == []  # nothing executes before the approver resumes
    as_profile("instructor")
    resume_approval(graph, "t2", True, "ok")
    assert len(graph.calls) == 1
    vals = graph.get_state({"configurable": {"thread_id": "t2"}}).values
    assert vals["approval"].approved is True
    assert vals["approval"].approver_id == "u_prof"  # from config, not payload


def test_reject_runs_nothing(graph, as_profile):
    submit("Write w", thread_id="t3", graph=graph)
    as_profile("instructor")
    resume_approval(graph, "t3", False, "no")
    assert graph.calls == []
    vals = graph.get_state({"configurable": {"thread_id": "t3"}}).values
    assert "Reviewer rejected" in vals["report"]


def test_self_approval_refused_and_stays_pending(graph, as_profile):
    as_profile("instructor")
    submit("Write w", thread_id="t4", graph=graph)  # instructor is the requester
    with pytest.raises(PermissionError):
        resume_approval(graph, "t4", True)
    assert graph.calls == []


def test_two_approvals_same_thread_keep_two_audit_rows(monkeypatch):
    monkeypatch.setattr(
        "codeforge.hitl.approval.interrupt",
        lambda payload: {"approved": False, "approver_id": "u_prof", "comment": "no"},
    )
    state = {"thread_id": "same", "code": RISKY, "identity": Identity(user_id="u_alice", role="student")}
    human_approval_node(dict(state))
    human_approval_node(dict(state))
    with init_db()() as s:
        rows = s.query(Approval).filter(Approval.thread_id == "same").all()
    assert len(rows) == 2
    assert len({r.approval_id for r in rows}) == 2


def test_expired_approval_rejected_fail_closed(graph):
    submit("Write w", thread_id="t5", graph=graph)
    later = datetime.now(UTC) + timedelta(minutes=45)
    assert expire_stale(graph, now=later) == ["t5"]
    assert graph.calls == []
    vals = graph.get_state({"configurable": {"thread_id": "t5"}}).values
    assert vals["approval"].approved is False
    assert "expired" in vals["approval"].comment
    # fresh approvals are untouched
    submit("Write w2", thread_id="t6", graph=graph)
    assert expire_stale(graph, now=datetime.now(UTC)) == []
