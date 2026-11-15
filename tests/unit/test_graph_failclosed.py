import sqlite3

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from codeforge.entrypoint import submit
from codeforge.graph import build_graph, route_after_policy
from codeforge.llm import AllModelsDown
from codeforge.state import GeneratedCode, GeneratedTests, Requirements, RouteDecision
from tests.fakes import FakeLLM

AGENTS = ("router", "requirements", "codegen", "testgen")
GOOD_TESTS = (
    "from solution import f\n\n"
    "def test_a():\n    assert f(1) == 2\n    assert f(2) == 3\n    assert f(3) == 4\n"
)


def _graph():
    return build_graph(checkpointer=SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))


def _patch(monkeypatch, fn):
    for a in AGENTS:
        monkeypatch.setattr(f"codeforge.agents.{a}.resilient_invoke", fn)


@pytest.fixture
def sandbox_calls(monkeypatch):
    calls = []
    import codeforge.graph as g
    real = g.run_pytest_sandboxed

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(g, "run_pytest_sandboxed", spy)
    return calls


def test_outage_unknown_task_never_sandboxed(monkeypatch, sandbox_calls):
    def down(agent, messages, schema):
        raise AllModelsDown("down")

    _patch(monkeypatch, down)
    res = submit("Write a function that does some obscure thing xyzzy", graph=_graph())
    assert sandbox_calls == []
    assert "cannot generate code right now" in res.values["report"]
    assert res.values["ticket"] is not None
    assert res.values["served_tier"] == "refused"


def test_outage_template_task_still_runs(monkeypatch, sandbox_calls):
    def down(agent, messages, schema):
        raise AllModelsDown("down")

    _patch(monkeypatch, down)
    res = submit("Write a palindrome function", graph=_graph())
    assert "✅ Tests passed" in res.values["report"]
    assert res.values["served_tier"] == "template"


def test_invalid_tests_never_reach_sandbox(monkeypatch, sandbox_calls):
    fake = FakeLLM()
    fake.set_response("router", RouteDecision(kind="python_function"))
    fake.set_response("requirements", Requirements(function_name="f"))
    fake.set_response("codegen", GeneratedCode(code="def f(x):\n    return x + 1\n"))
    fake.set_response("testgen", GeneratedTests(tests="from solution import f\ndef test_a():\n    assert f(1) == 2\n"))
    _patch(monkeypatch, fake)
    res = submit("Write f", graph=_graph())
    assert sandbox_calls == []
    assert "tests were invalid" in res.values["report"]
    assert res.values["ticket"] is not None
    assert sum(1 for i in fake.invocations if i["agent"] == "testgen") == 2  # regenerated once


def test_repair_loop_freezes_tests(monkeypatch):
    fake = FakeLLM()
    fake.set_response("router", RouteDecision(kind="python_function"))
    fake.set_response("requirements", Requirements(function_name="f"))
    fake.set_response("testgen", GeneratedTests(tests=GOOD_TESTS))
    codes = iter([
        GeneratedCode(code="def f(x):\n    return x\n"),      # buggy
        GeneratedCode(code="def f(x):\n    return x + 1\n"),  # fixed
    ])

    def fn(agent, messages, schema):
        if agent == "codegen":
            return next(codes), "primary"
        return fake(agent, messages, schema)

    _patch(monkeypatch, fn)
    res = submit("Write f", graph=_graph())
    assert "✅ Tests passed" in res.values["report"]
    assert sum(1 for i in fake.invocations if i["agent"] == "testgen") == 1
    assert res.values["tests"] == GOOD_TESTS


def test_missing_policy_fails_closed():
    assert route_after_policy({}) == "refuse"


def test_tests_that_disagree_with_reference_are_regenerated_once(monkeypatch):
    fake = FakeLLM()
    fake.set_response("router", RouteDecision(kind="python_function"))
    fake.set_response("requirements", Requirements(function_name="is_palindrome"))
    fake.set_response(
        "codegen",
        GeneratedCode(code="def is_palindrome(s):\n    return s == s[::-1]\n"),
    )
    wrong = (
        "from solution import is_palindrome\n\n"
        "def test_a():\n    assert is_palindrome('abc') is True\n"
        "    assert is_palindrome('radar') is True\n    assert is_palindrome('x') is True\n"
    )
    fake.set_response("testgen", GeneratedTests(tests=wrong))
    _patch(monkeypatch, fake)
    res = submit("Write a palindrome checker", graph=_graph())
    assert sum(1 for i in fake.invocations if i["agent"] == "testgen") == 2
    stages = [e for e in res.values["events"] if e["stage"] == "test_validator"]
    assert stages[0]["is_valid"] is False
    assert "reference" in stages[0]["reason"]
    assert "accepted with warning" in stages[1]["reason"]


def test_student_c_gets_p1_ticket_without_secrets(monkeypatch, sandbox_calls):
    from codeforge.store.db import Ticket, init_db

    res = submit(
        "THIS IS NOT WORKING!!! get me a human. key sk-abcdefghijklmnop1234 aadhaar 2345 6789 0124",
        graph=_graph(),
    )
    t = res.values["ticket"]
    assert t.priority == "P1"
    assert "sk-" not in t.request_masked and "[SECRET]" in t.request_masked
    assert "escalated to a human" in res.values["report"]
    assert sandbox_calls == []
    with init_db()() as s:
        rows = s.query(Ticket).all()
    assert all("sk-" not in r.request_masked for r in rows)


def test_second_policy_denial_opens_ticket(monkeypatch):
    fake = FakeLLM()
    fake.set_response("router", RouteDecision(kind="python_function"))
    fake.set_response("requirements", Requirements(function_name="f"))
    fake.set_response("codegen", GeneratedCode(code="import os\n\ndef f():\n    os.remove('x')\n"))
    fake.set_response("testgen", GeneratedTests(tests=GOOD_TESTS))
    _patch(monkeypatch, fake)
    g = _graph()
    first = submit("delete my files", thread_id="d1", graph=g)
    assert first.values.get("ticket") is None
    assert "Refused by policy" in first.values["report"]
    second = submit("delete all my files now", thread_id="d2", graph=g)
    assert second.values["ticket"] is not None
    assert "Repeated policy denials" in second.values["ticket"].reason
