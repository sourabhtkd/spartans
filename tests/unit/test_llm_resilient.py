import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from codeforge import llm
from codeforge.cost.metering import MeteringCallback
from codeforge.resilience.breaker import CircuitBreaker
from codeforge.resilience.retry import is_transient
from codeforge.state import RouteDecision
from codeforge.store.db import Metric, init_db


class RateLimitError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class WithStatus(Exception):
    def __init__(self, code):
        super().__init__("boom")
        self.status_code = code


def test_classifier_by_class_status_and_message():
    assert is_transient(RateLimitError("x"))
    assert not is_transient(AuthenticationError("x"))
    assert is_transient(WithStatus(503))
    assert not is_transient(WithStatus(404))
    assert is_transient(Exception("Error code: 429 - {'error': 'rate'}"))
    assert not is_transient(Exception("failed to process 500 rows"))
    assert not is_transient(Exception("401 Unauthorized"))
    assert is_transient(TimeoutError("read"))


def test_breaker_failure_window():
    t = [0.0]
    cb = CircuitBreaker("w", failure_threshold=3, cooldown_s=10, clock=lambda: t[0], window_s=5)
    cb.record_failure()
    t[0] = 10.0  # first failure is now outside the window
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "CLOSED"
    cb.record_failure()
    assert cb.state == "OPEN"


def test_breakers_built_from_config():
    llm.reset_breakers()
    b = llm.get_breaker("primary")
    assert (b.failure_threshold, b.cooldown_s, b.window_s) == (5, 30.0, 60.0)


def test_metering_callback_records_real_usage_and_cost():
    from codeforge.config import get_settings
    from codeforge.cost.metering import compute_cost

    large = get_settings().models["large"]  # prices come from config.yaml, never hardcoded
    cb = MeteringCallback("codegen", large.name, request_id="req1")
    cb.on_chat_model_start({}, [[]])
    msg = AIMessage(content="x", usage_metadata={"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500})
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]))
    with init_db()() as s:
        row = s.query(Metric).one()
    assert (row.request_id, row.agent, row.input_tokens, row.output_tokens) == ("req1", "codegen", 1000, 500)
    assert row.cost_usd == pytest.approx((1000 * large.in_per_1k + 500 * large.out_per_1k) / 1000)
    assert compute_cost("unknown-model", 1, 1) == 0.0


class _FakeStructured:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = 0

    def invoke(self, messages, config=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        for cb in config["callbacks"]:
            cb.on_chat_model_start({}, [[]])
            msg = AIMessage(content="x", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
            cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]))
        return {"parsed": outcome, "raw": None, "parsing_error": None}


class _FakeLLM:
    def __init__(self, outcomes):
        self.structured = _FakeStructured(outcomes)

    def with_structured_output(self, schema, include_raw=False):
        assert include_raw is True
        return self.structured


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    llm.reset_breakers()
    yield
    llm.reset_breakers()


def _install(monkeypatch, per_tier):
    fakes = {t: _FakeLLM(o) for t, o in per_tier.items()}
    monkeypatch.setattr(llm, "get_llm_for_tier", lambda tier: fakes[tier])
    return fakes


def test_retries_transient_then_succeeds_and_meters(monkeypatch, no_sleep):
    ok = RouteDecision(kind="python_function")
    fakes = _install(monkeypatch, {"small": [RateLimitError("429"), ok]})
    res, tier = llm.resilient_invoke("router", ["m"], RouteDecision)
    assert tier == "primary" and res.kind == "python_function"
    assert fakes["small"].structured.calls == 2
    with init_db()() as s:
        assert s.query(Metric).count() == 1


def test_permanent_error_fails_fast_to_fallback(monkeypatch, no_sleep):
    ok = RouteDecision(kind="other")
    fakes = _install(monkeypatch, {"small": [AuthenticationError("401")], "fallback": [ok]})
    _res, tier = llm.resilient_invoke("router", ["m"], RouteDecision)
    assert tier == "fallback"
    assert fakes["small"].structured.calls == 1  # no retries on auth errors


def test_all_down_raises(monkeypatch, no_sleep):
    _install(monkeypatch, {"small": [RateLimitError("429")] * 3, "fallback": [RateLimitError("429")] * 3})
    with pytest.raises(llm.AllModelsDown):
        llm.resilient_invoke("router", ["m"], RouteDecision)


def test_missing_api_key_raises_all_down_without_network(no_sleep, monkeypatch):
    llm.clear_llm_cache()
    monkeypatch.setenv("GROQ_API_KEY", "")
    from codeforge.config import clear_settings_cache

    clear_settings_cache()
    with pytest.raises(llm.AllModelsDown):
        llm.resilient_invoke("router", ["m"], RouteDecision)
    llm.clear_llm_cache()
