import pytest

from codeforge.state import Requirements, RouteDecision
from tests.fakes import FakeLLM


def test_fake_llm_structured_output(monkeypatch: pytest.MonkeyPatch):
    fake = FakeLLM()
    fake.set_response(
        "router",
        RouteDecision(kind="python_function", language="python", confidence=0.99),
    )
    fake.set_response(
        "requirements",
        Requirements(function_name="is_palindrome", returns="bool"),
    )

    monkeypatch.setattr("codeforge.llm.resilient_invoke", fake)

    from codeforge.llm import resilient_invoke

    resp1, tier1 = resilient_invoke("router", ["test message"], RouteDecision)
    assert resp1.kind == "python_function"
    assert tier1 == "primary"

    resp2, tier2 = resilient_invoke("requirements", ["extract"], Requirements)
    assert resp2.function_name == "is_palindrome"
    assert resp2.returns == "bool"
    assert tier2 == "primary"

    assert len(fake.invocations) == 2
    assert fake.invocations[0]["agent"] == "router"
