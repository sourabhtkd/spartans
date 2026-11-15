import random

import pytest

from codeforge.config import clear_settings_cache
from codeforge.graph import build_graph
from codeforge.resilience.breaker import CircuitBreaker
from codeforge.resilience.retry import (
    calculate_backoff,
    get_retry_after,
    is_transient,
)
from codeforge.resilience.templates import match_template


def test_transient_error_classification():
    assert is_transient(Exception("HTTP 429 Too Many Requests"))
    assert is_transient(Exception("503 Service Unavailable"))
    assert is_transient(Exception("Connection reset by peer"))
    assert is_transient(Exception("Read timed out"))

    # Permanent errors
    assert not is_transient(Exception("401 Unauthorized: Invalid API key"))
    assert not is_transient(Exception("403 Forbidden"))
    assert not is_transient(ValueError("Pydantic validation failed"))


def test_retry_after_extraction():
    class FakeErrWithHeaders(Exception):
        def __init__(self, msg: str) -> None:
            super().__init__(msg)
            self.headers = {"retry-after": "5.5"}

    assert get_retry_after(FakeErrWithHeaders("rate limit")) == 5.5
    assert get_retry_after(Exception("Rate limit reached. Retry-After: 12 seconds")) == 12.0
    assert get_retry_after(Exception("Just a standard failure")) is None


def test_full_jitter_bounds():
    rng = random.Random(42)
    for attempt in range(5):
        val = calculate_backoff(attempt, base_s=0.5, cap_s=8.0, rng=rng)
        max_possible = min(8.0, 0.5 * (2**attempt))
        assert 0.0 <= val <= max_possible


def test_circuit_breaker_transitions():
    fake_time = 100.0

    def clock():
        return fake_time

    cb = CircuitBreaker("test_breaker", failure_threshold=3, cooldown_s=10.0, clock=clock)
    assert cb.state == "CLOSED"
    assert cb.can_attempt() is True

    # 1st failure
    cb.record_failure()
    assert cb.state == "CLOSED"

    # 2nd failure
    cb.record_failure()
    assert cb.state == "CLOSED"

    # 3rd failure -> trips to OPEN
    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.can_attempt() is False

    # Advance clock by 5s (< cooldown 10s)
    fake_time += 5.0
    assert cb.can_attempt() is False

    # Advance clock past cooldown (100 + 11 = 111s)
    fake_time += 6.0
    # Probe allowed -> transitions to HALF_OPEN
    assert cb.can_attempt() is True
    assert cb.state == "HALF_OPEN"
    # Second concurrent attempt before probe completes is rejected
    assert cb.can_attempt() is False

    # Probe succeeds -> CLOSED
    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 0


def test_template_matching():
    tpl1 = match_template("write an is_palindrome function")
    assert tpl1 is not None
    assert tpl1.name == "palindrome"

    tpl2 = match_template("calculate factorial of n")
    assert tpl2 is not None
    assert tpl2.name == "factorial"

    tpl3 = match_template("fizzbuzz 15")
    assert tpl3 is not None
    assert tpl3.name == "fizzbuzz"

    tpl_none = match_template("completely bespoke custom quantum algorithm")
    assert tpl_none is None


def test_outage_template_vs_unknown_task(monkeypatch: pytest.MonkeyPatch):
    clear_settings_cache()
    monkeypatch.setenv("TOTAL_OUTAGE", "true")
    clear_settings_cache()

    # Isolate semantic cache with fresh in-memory DB
    from codeforge.cost.semantic_cache import SemanticCache
    from codeforge.store.db import init_db

    mem_cache = SemanticCache(session_factory=init_db("sqlite:///:memory:"))
    monkeypatch.setattr("codeforge.cost.semantic_cache.get_semantic_cache", lambda: mem_cache)

    graph = build_graph()

    # 1. Template task during total outage -> serves template & passes sandbox tests!
    res_tpl = graph.invoke(
        {"raw_request_masked": "Write an is_palindrome function", "thread_id": "outage_tpl"},
        config={"configurable": {"thread_id": "outage_tpl"}},
    )
    assert res_tpl.get("served_tier") == "template"
    assert res_tpl.get("run_result") is not None
    assert res_tpl.get("run_result").exit_code == 0
    assert "✅ Tests passed" in res_tpl.get("report", "")

    # 2. Unknown task during total outage -> honest refusal + support ticket!
    res_unknown = graph.invoke(
        {"raw_request_masked": "Implement advanced ray tracing BVH algorithm", "thread_id": "outage_unknown"},
        config={"configurable": {"thread_id": "outage_unknown"}},
    )
    assert res_unknown.get("served_tier") == "refused"
    assert "cannot generate code right now" in res_unknown.get("report", "")
    assert res_unknown.get("ticket") is not None
