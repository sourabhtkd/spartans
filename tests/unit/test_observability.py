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
            calls["metadata"] = kw.get("metadata")

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
    monkeypatch.setattr(obs, "langfuse_handler", lambda: None)
    monkeypatch.setattr(obs, "flush_langfuse", lambda: None)
    outage(monkeypatch)

    result = entrypoint.submit("Write an is_palindrome function", session_id="conv_9", graph=memory_graph())

    assert result.trace_id == "trace_abc"
    assert calls["propagate"]["session_id"] == "conv_9" and calls["propagate"]["user_id"] == "u_alice"
    assert "student" in calls["propagate"]["tags"]
    assert calls["start"]["name"] == "chat_turn" and "palindrome" in calls["start"]["input"]
    assert "Tests passed" in calls["output"]
    assert calls["metadata"]["served_by"]  # run outcome facts land on the trace

    assert obs.score_trace("trace_abc", "user_feedback", 1.0, "nice") is True
    assert calls["score"]["trace_id"] == "trace_abc" and calls["score"]["value"] == 1.0


def test_llm_call_extends_the_parent_callbacks_instead_of_replacing_them(monkeypatch):
    """Inside a graph node the LLM call must keep the node's (Langfuse) handlers, plus metering."""
    from langchain_core.callbacks import BaseCallbackHandler, CallbackManager
    from langchain_core.runnables.config import var_child_runnable_config

    from codeforge import llm
    from codeforge.cost.metering import MeteringCallback

    class ParentHandler(BaseCallbackHandler):
        pass

    parent = ParentHandler()
    created: list = []
    monkeypatch.setattr(llm, "has_langfuse_handler", lambda cbs: True)
    monkeypatch.setattr(llm, "langfuse_handler", lambda: created.append(1))
    token = var_child_runnable_config.set({"callbacks": CallbackManager(handlers=[parent])})
    try:
        cbs = llm._call_callbacks("codegen", "small")
    finally:
        var_child_runnable_config.reset(token)
    assert parent in cbs.handlers
    assert any(isinstance(h, MeteringCallback) for h in cbs.handlers)
    assert created == []  # no duplicate Langfuse handler when the parent already has one


def test_sync_model_prices_registers_config_prices_per_token(monkeypatch):
    import langfuse

    import codeforge.observability as obs
    from codeforge.config import get_settings

    created: list = []

    class Page:
        data = (type("M", (), {"model_name": "llama-3.1-8b-instant"})(),)
        meta = type("Meta", (), {"total_pages": 1})()

    class Models:
        def list(self, **kw):
            return Page()

        def create(self, **kw):
            created.append(kw)

    class Client:
        api = type("Api", (), {"models": Models()})()

    monkeypatch.setattr(langfuse, "get_client", lambda: Client())
    n = obs.sync_model_prices()
    names = {m.name for m in get_settings().models.values()} - {"llama-3.1-8b-instant"}
    assert n == len(names) == len(created)
    for kw in created:
        model = next(m for m in get_settings().models.values() if m.name == kw["model_name"])
        assert kw["input_price"] == model.in_per_1k / 1000 and kw["unit"] == "TOKENS"
        assert kw["match_pattern"].startswith("(?i)^")
