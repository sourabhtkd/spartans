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
    from tests.helpers import memory_graph, patch_llm, scripted_fake

    patch_llm(monkeypatch, scripted_fake("def f(x):\n    return x + 1\n"))

    secret = "sk-abcdefghijklmnop1234"
    submit(f"Write f. my key {secret}", thread_id="obs1", graph=memory_graph())
    err = capsys.readouterr().err
    events = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    assert secret not in err and "sk-" not in err
    ends = [e for e in events if e["event"] == "node_end"]
    assert {"ingress_guard", "router", "policy_gate", "sandbox_run", "report"} <= {e["node"] for e in ends}
    assert all(e.get("thread_id") == "obs1" and e.get("user_id") == "u_alice" for e in ends)
    assert any(e.get("policy_verdict") == "allow" for e in ends)
