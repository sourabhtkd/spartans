import pytest

from codeforge.agents.codegen import CodeGenAgent
from codeforge.agents.evaluator import EvaluatorAgent
from codeforge.agents.requirements import RequirementsAgent
from codeforge.agents.router import RouterAgent
from codeforge.agents.test_validator import validate_tests
from codeforge.agents.testgen import TestGenAgent
from codeforge.state import (
    CodeForgeState,
    GeneratedCode,
    GeneratedTests,
    Requirements,
    RouteDecision,
    RunResult,
)
from tests.fakes import FakeLLM


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    fake = FakeLLM()
    fake.set_response(
        "router",
        RouteDecision(kind="python_function", language="python", confidence=0.98),
    )
    fake.set_response(
        "requirements",
        Requirements(
            function_name="is_palindrome",
            parameters=[],
            returns="bool",
            edge_cases=["empty string"],
            description="Check palindrome",
        ),
    )
    fake.set_response(
        "codegen",
        GeneratedCode(code="def is_palindrome(s: str) -> bool:\n    return s == s[::-1]\n"),
    )
    fake.set_response(
        "testgen",
        GeneratedTests(
            tests=(
                "from solution import is_palindrome\n\n"
                "def test_palindrome():\n"
                "    assert is_palindrome('radar') is True\n"
                "    assert is_palindrome('hello') is False\n"
                "    assert is_palindrome('') is True\n"
            )
        ),
    )
    monkeypatch.setattr("codeforge.agents.router.resilient_invoke", fake)
    monkeypatch.setattr("codeforge.agents.requirements.resilient_invoke", fake)
    monkeypatch.setattr("codeforge.agents.codegen.resilient_invoke", fake)
    monkeypatch.setattr("codeforge.agents.testgen.resilient_invoke", fake)
    return fake


def test_router_agent(fake_llm: FakeLLM):
    agent = RouterAgent()
    state: CodeForgeState = {"request_masked": "Write a palindrome function"}
    result = agent(state)
    assert result["route"].kind == "python_function"


def test_requirements_agent(fake_llm: FakeLLM):
    agent = RequirementsAgent()
    state: CodeForgeState = {"request_masked": "Write a palindrome function"}
    result = agent(state)
    assert result["requirements"].function_name == "is_palindrome"


def test_codegen_agent_initial_and_repair(fake_llm: FakeLLM):
    agent = CodeGenAgent()
    state: CodeForgeState = {
        "request_masked": "Write a palindrome function",
        "requirements": Requirements(function_name="is_palindrome"),
        "attempts": 0,
    }
    result1 = agent(state)
    assert "def is_palindrome" in result1["code"]
    assert result1["attempts"] == 1

    # Repair attempt
    state["attempts"] = 1
    state["code"] = "def is_palindrome(s):\n    return False"
    state["run_result"] = RunResult(
        last_failing_test="test_palindrome failed on 'radar'",
        failed=1,
    )
    result2 = agent(state)
    assert result2["attempts"] == 2
    # Check that invocation message had repair instructions
    last_invoc = fake_llm.invocations[-1]
    assert "REPAIR ATTEMPT #2" in last_invoc["messages"][-1]["content"]


def test_testgen_agent(fake_llm: FakeLLM):
    agent = TestGenAgent()
    state: CodeForgeState = {
        "requirements": Requirements(function_name="is_palindrome"),
    }
    result = agent(state)
    assert "from solution import is_palindrome" in result["tests"]


def test_test_validator():
    valid_tests = (
        "from solution import is_palindrome\n\n"
        "def test_cases():\n"
        "    assert is_palindrome('a')\n"
        "    assert not is_palindrome('ab')\n"
        "    assert is_palindrome('')\n"
    )
    is_valid, reason = validate_tests(valid_tests, "is_palindrome")
    assert is_valid is True
    assert reason == "valid"

    # Missing target import
    missing_import = (
        "def test_cases():\n"
        "    assert 1 == 1\n"
        "    assert 2 == 2\n"
        "    assert 3 == 3\n"
    )
    ok, err = validate_tests(missing_import, "is_palindrome")
    assert ok is False
    assert "do not import" in err

    # Too few asserts
    few_asserts = (
        "from solution import is_palindrome\n\n"
        "def test_cases():\n"
        "    assert is_palindrome('a')\n"
    )
    ok2, err2 = validate_tests(few_asserts, "is_palindrome")
    assert ok2 is False
    assert "assert statements" in err2


def test_evaluator_agent():
    agent = EvaluatorAgent()
    state: CodeForgeState = {
        "code": "def is_palindrome(s: str) -> bool: return s == s[::-1]",
        "requirements": Requirements(function_name="is_palindrome", edge_cases=["empty"]),
        "events": [],
    }
    result = agent(state)
    assert len(result["events"]) == 1
    assert result["events"][0]["stage"] == "evaluator_advisory"
