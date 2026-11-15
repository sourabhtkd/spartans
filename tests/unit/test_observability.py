import json

import pytest

from codeforge.observability import get_logger


def test_logger_masks_pii(capsys: pytest.CaptureFixture[str]):
    logger = get_logger("test_obs")
    raw_secret = "sk-abcdef1234567890XYZ"
    logger.info("user_event", secret_key=raw_secret, email="user@domain.com")

    captured = capsys.readouterr()
    output = captured.err or captured.out
    assert raw_secret not in output
    assert "user@domain.com" not in output
    assert "[SECRET]" in output
    assert "[EMAIL]" in output

    parsed = json.loads(output.strip().splitlines()[-1])
    assert parsed["secret_key"] == "[SECRET]"
    assert parsed["email"] == "[EMAIL]"


def test_full_graph_logs_are_structured_bound_and_pii_free(capsys, monkeypatch):
    import json

    from codeforge.entrypoint import submit
    from codeforge.pipeline import build_graph
    from codeforge.state import (
        GeneratedCode,
        GeneratedTests,
        Requirements,
        RouteDecision,
    )
    from tests.fakes import FakeLLM

    fake = FakeLLM()
    fake.set_response("router", RouteDecision(kind="python_function"))
    fake.set_response("requirements", Requirements(function_name="f"))
    fake.set_response("codegen", GeneratedCode(code="def f(x):\n    return x + 1\n"))
    fake.set_response(
        "testgen",
        GeneratedTests(tests="from solution import f\n\ndef test_a():\n    assert f(1) == 2\n    assert f(2) == 3\n    assert f(3) == 4\n"),
    )
    for a in ("router", "requirements", "codegen", "testgen"):
        monkeypatch.setattr(f"codeforge.agents.{a}.resilient_invoke", fake)

    secret = "sk-abcdefghijklmnop1234"
    submit(f"Write f. my key {secret}", thread_id="obs1", graph=build_graph())
    err = capsys.readouterr().err
    events = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    assert secret not in err and "sk-" not in err
    ends = [e for e in events if e["event"] == "node_end"]
    assert {"ingress_guard", "router", "policy_gate", "sandbox_run", "report"} <= {e["node"] for e in ends}
    assert all(e.get("thread_id") == "obs1" and e.get("user_id") == "u_alice" for e in ends)
    assert any(e.get("policy_verdict") == "allow" for e in ends)
