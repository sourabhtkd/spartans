import sqlite3

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from codeforge.hitl.approval import resume_approval
from codeforge.pipeline import build_graph
from codeforge.state import (
    GeneratedCode,
    GeneratedTests,
    Requirements,
    RouteDecision,
)
from tests.fakes import FakeLLM


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    fake = FakeLLM()
    fake.set_response(
        "router",
        RouteDecision(kind="python_function", language="python", confidence=0.99),
    )
    fake.set_response(
        "requirements",
        Requirements(function_name="is_palindrome", returns="bool"),
    )
    fake.set_response(
        "codegen",
        GeneratedCode(
            code="def is_palindrome(s: str) -> bool:\n    return s == s[::-1]\n"
        ),
    )
    fake.set_response(
        "testgen",
        GeneratedTests(
            tests=(
                "from solution import is_palindrome\n\n"
                "def test_cases():\n"
                "    assert is_palindrome('radar') is True\n"
                "    assert is_palindrome('noon') is True\n"
                "    assert is_palindrome('world') is False\n"
            )
        ),
    )
    monkeypatch.setattr("codeforge.agents.router.resilient_invoke", fake)
    monkeypatch.setattr("codeforge.agents.requirements.resilient_invoke", fake)
    monkeypatch.setattr("codeforge.agents.codegen.resilient_invoke", fake)
    monkeypatch.setattr("codeforge.agents.testgen.resilient_invoke", fake)
    return fake


def test_graph_end_to_end_passing(fake_llm: FakeLLM):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    graph = build_graph(checkpointer=checkpointer)

    thread_id = "test_th_pass"
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {
        "request_masked": "Write a palindrome function",
        "thread_id": thread_id,
    }

    for _ in graph.stream(initial_state, config=config):
        pass

    state = graph.get_state(config)
    report = state.values.get("report", "")
    assert "✅ Tests passed" in report
    assert state.values.get("run_result").exit_code == 0


def test_graph_policy_denied(fake_llm: FakeLLM):
    fake_llm.set_response(
        "codegen",
        GeneratedCode(code="import os\nos.remove('/tmp/foo')\n"),
    )
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    graph = build_graph(checkpointer=checkpointer)

    thread_id = "test_th_deny"
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {
        "request_masked": "Delete /tmp/foo",
        "thread_id": thread_id,
    }

    for _ in graph.stream(initial_state, config=config):
        pass

    state = graph.get_state(config)
    report = state.values.get("report", "")
    assert "❌ Refused by policy" in report
    assert state.values.get("run_result") is None


def test_graph_interrupt_and_resume(fake_llm: FakeLLM, as_profile):
    # Needs approval code (e.g. open file)
    fake_llm.set_response(
        "codegen",
        GeneratedCode(
            code=(
                "def write_file():\n"
                "    with open('out.txt', 'w') as f:\n"
                "        f.write('data')\n"
            )
        ),
    )
    fake_llm.set_response(
        "testgen",
        GeneratedTests(
            tests=(
                "from solution import write_file\n\n"
                "def test_write():\n"
                "    write_file()\n"
                "    assert True\n"
                "    assert 1 == 1\n"
                "    assert 2 == 2\n"
            )
        ),
    )
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    graph = build_graph(checkpointer=checkpointer)

    thread_id = "test_th_approval"
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {
        "request_masked": "Write to file",
        "thread_id": thread_id,
    }

    # Stream should pause at human_approval interrupt
    for _ in graph.stream(initial_state, config=config):
        pass

    state = graph.get_state(config)
    assert state.next and "human_approval" in state.next

    # Resume with instructor approval
    as_profile("instructor")  # approver identity comes from server-side config
    resume_approval(graph, thread_id, True, "Approved by professor")

    final_state = graph.get_state(config)
    assert final_state.values.get("approval").approved is True
    assert final_state.values.get("run_result") is not None
