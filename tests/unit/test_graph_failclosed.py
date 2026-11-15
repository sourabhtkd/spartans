"""Pipeline behaviour when things go wrong: nothing unsafe or invalid may run, and users are told honestly."""
from codeforge.agents.evaluator import EvaluationCritique
from codeforge.entrypoint import submit
from codeforge.pipeline.routing import route_after_policy
from codeforge.state import GeneratedCode
from codeforge.store.db import Ticket, init_db
from tests.fakes import FakeLLM
from tests.helpers import GOOD_TESTS, memory_graph, outage, patch_llm, scripted_fake


def count_testgen_calls(fake: FakeLLM) -> int:
    return sum(1 for call in fake.invocations if call["agent"] == "testgen")


def test_outage_unknown_task_never_sandboxed(monkeypatch, sandbox_calls):
    outage(monkeypatch)
    res = submit("Write a function that does some obscure thing xyzzy", graph=memory_graph())
    assert sandbox_calls == []
    assert "cannot generate code right now" in res.values["report"]
    assert res.values["ticket"] is not None
    assert res.values["served_by"] == "refused"


def test_outage_template_task_still_runs(monkeypatch, sandbox_calls):
    outage(monkeypatch)
    res = submit("Write a palindrome function", graph=memory_graph())
    assert "✅ Tests passed" in res.values["report"]
    assert res.values["served_by"] == "template"


def test_invalid_tests_never_reach_sandbox(monkeypatch, sandbox_calls):
    one_assert = "from solution import f\ndef test_a():\n    assert f(1) == 2\n"
    fake = scripted_fake("def f(x):\n    return x + 1\n", tests=one_assert)
    patch_llm(monkeypatch, fake)
    res = submit("Write f", graph=memory_graph())
    assert sandbox_calls == []
    assert "tests were invalid" in res.values["report"]
    assert res.values["ticket"] is not None
    assert count_testgen_calls(fake) == 2  # regenerated once


def test_repair_loop_freezes_tests(monkeypatch):
    fake = scripted_fake(code="unused: codegen is scripted below")
    buggy_then_fixed = iter([
        GeneratedCode(code="def f(x):\n    return x\n"),
        GeneratedCode(code="def f(x):\n    return x + 1\n"),
    ])

    def model(agent, messages, schema):
        if agent == "codegen":
            return next(buggy_then_fixed), "primary"
        return fake(agent, messages, schema)

    patch_llm(monkeypatch, model)
    res = submit("Write f", graph=memory_graph())
    assert "✅ Tests passed" in res.values["report"]
    assert count_testgen_calls(fake) == 1  # the repair loop only rewrote the code
    assert res.values["tests"] == GOOD_TESTS


def test_missing_policy_fails_closed():
    assert route_after_policy({}) == "refuse"


def test_tests_that_disagree_with_reference_are_regenerated_once(monkeypatch):
    wrong_tests = (
        "from solution import is_palindrome\n\n"
        "def test_a():\n    assert is_palindrome('abc') is True\n"
        "    assert is_palindrome('radar') is True\n    assert is_palindrome('x') is True\n"
    )
    fake = scripted_fake(
        "def is_palindrome(s):\n    return s == s[::-1]\n", tests=wrong_tests, fn_name="is_palindrome"
    )
    patch_llm(monkeypatch, fake)
    res = submit("Write a palindrome checker", graph=memory_graph())
    assert count_testgen_calls(fake) == 2
    first, second = [e for e in res.values["events"] if e["stage"] == "test_validator"]
    assert first["is_valid"] is False and "reference" in first["reason"]
    assert "accepted with warning" in second["reason"]


def test_student_c_gets_p1_ticket_without_secrets(sandbox_calls):
    res = submit(
        "THIS IS NOT WORKING!!! get me a human. key sk-abcdefghijklmnop1234 aadhaar 2345 6789 0124",
        graph=memory_graph(),
    )
    ticket = res.values["ticket"]
    assert ticket.priority == "P1"
    assert "sk-" not in ticket.request_masked and "[SECRET]" in ticket.request_masked
    assert "escalated to a human" in res.values["report"]
    assert sandbox_calls == []
    with init_db()() as session:
        assert all("sk-" not in row.request_masked for row in session.query(Ticket).all())


def test_second_policy_denial_opens_ticket(monkeypatch):
    patch_llm(monkeypatch, scripted_fake("import os\n\ndef f():\n    os.remove('x')\n"))
    graph = memory_graph()
    first = submit("delete my files", thread_id="d1", graph=graph)
    assert first.values.get("ticket") is None
    assert "Refused by policy" in first.values["report"]
    second = submit("delete all my files now", thread_id="d2", graph=graph)
    assert second.values["ticket"] is not None
    assert "Repeated policy denials" in second.values["ticket"].reason


def happy_path(monkeypatch) -> FakeLLM:
    fake = scripted_fake("def f(x):\n    return x + 1\n")
    patch_llm(monkeypatch, fake)
    return fake


def test_evaluator_is_advisory_and_never_changes_status(monkeypatch):
    happy_path(monkeypatch)
    critique = EvaluationCritique(clarity_score=0.0, advisory_notes=["This looks wrong!"])
    monkeypatch.setattr(
        "codeforge.agents.evaluator.resilient_invoke", lambda agent, messages, schema: (critique, "primary")
    )
    res = submit("Write f", graph=memory_graph())
    assert "✅ Tests passed" in res.values["report"]  # status comes only from pytest
    assert "This looks wrong!" in res.values["report"]
    assert any(e["stage"] == "evaluator_advisory" for e in res.values["events"])


def test_evaluator_without_model_says_unavailable(monkeypatch):
    happy_path(monkeypatch)
    res = submit("Write f", graph=memory_graph())
    event = next(e for e in res.values["events"] if e["stage"] == "evaluator_advisory")
    assert event["source"] == "unavailable"
    assert "unavailable" in res.values["report"]


def test_long_history_is_compacted_masked_and_skips_cache(monkeypatch):
    fake = happy_path(monkeypatch)
    history = [{"role": "user", "content": f"turn {i} key sk-abcdefghijklmnop1234"} for i in range(10)]
    res = submit("Write f", graph=memory_graph(), history=history)
    compacted = res.values["history"]
    assert len(compacted) == 5 and compacted[0]["role"] == "system"
    assert "Previous context summary" in compacted[0]["content"] and "sk-" not in compacted[0]["content"]
    assert res.values.get("cache_hit") is False
    requirements_messages = next(c for c in fake.invocations if c["agent"] == "requirements")["messages"]
    assert "Conversation so far" in requirements_messages[-1]["content"]
