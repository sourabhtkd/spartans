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

    secret = "sk-abcdefghijklmnop1234" #gitleaks:allow
    submit(f"Write f. my key {secret}", thread_id="obs1", graph=memory_graph())
    err = capsys.readouterr().err
    events = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    assert secret not in err and "sk-" not in err
    ends = [e for e in events if e["event"] == "node_end"]
    assert {"ingress_guard", "router", "policy_gate", "sandbox_run", "report"} <= {e["node"] for e in ends}
    assert all(e.get("thread_id") == "obs1" and e.get("user_id") == "u_alice" for e in ends)
    assert any(e.get("policy_verdict") == "allow" for e in ends)


def test_langfuse_is_a_noop_without_keys():
    from codeforge.observability import langfuse_handler, score_trace, trace_turn

    assert langfuse_handler() is None
    with trace_turn(session_id="conv_x", user_id="u_alice", name="chat_turn", tags=[], input="hi") as turn:
        turn.output = "reply"
    assert turn.trace_id == ""
    assert score_trace("t1", "user_feedback", 1.0) is False


def test_langfuse_session_user_and_trace_id_flow_through_a_turn(monkeypatch):
    """With tracing 'on' (stubbed client), one trace per turn carries session/user and is scored."""
    import contextlib

    import codeforge.observability as obs
    from codeforge import entrypoint
    from tests.helpers import memory_graph, outage

    calls: dict = {}

    class FakeSpan:
        def update(self, **kw):
            calls["output"] = kw["output"]

    class FakeClient:
        def start_as_current_observation(self, **kw):
            calls["start"] = kw
            return contextlib.nullcontext(FakeSpan())

        def get_current_trace_id(self):
            return "trace_abc"

        def create_score(self, **kw):
            calls["score"] = kw

    @contextlib.contextmanager
    def fake_propagate(**kw):
        calls["propagate"] = kw
        yield

    import langfuse

    monkeypatch.setattr(obs, "get_langfuse_client", lambda: FakeClient())
    monkeypatch.setattr(langfuse, "propagate_attributes", fake_propagate)
    monkeypatch.setattr(entrypoint, "langfuse_handler", lambda: None)
    outage(monkeypatch)

    result = entrypoint.submit("Write an is_palindrome function", session_id="conv_9", graph=memory_graph())

    assert result.trace_id == "trace_abc"
    assert calls["propagate"]["session_id"] == "conv_9" and calls["propagate"]["user_id"] == "u_alice"
    assert "student" in calls["propagate"]["tags"]
    assert calls["start"]["name"] == "chat_turn" and "palindrome" in calls["start"]["input"]
    assert "Tests passed" in calls["output"]

    assert obs.score_trace("trace_abc", "user_feedback", 1.0, "nice") is True
    assert calls["score"]["trace_id"] == "trace_abc" and calls["score"]["value"] == 1.0
