"""The single resilient LLM call path.

Every model call in CodeForge goes through ``resilient_invoke``:
chaos injection -> per-tier circuit breaker -> full-jitter retries (transient errors only)
-> fallback model -> AllModelsDown (callers then use templates or refuse honestly).
Metering and Langfuse tracing are attached to every call.
"""
import threading
import time
from functools import lru_cache
from typing import Any, TypeVar, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from codeforge.config import get_settings
from codeforge.cost.metering import MeteringCallback
from codeforge.observability import current_request_id, get_logger, langfuse_handler
from codeforge.resilience.breaker import CircuitBreaker
from codeforge.resilience.chaos import maybe_fail
from codeforge.resilience.retry import calculate_backoff, get_retry_after, is_transient

logger = get_logger("llm")

T = TypeVar("T", bound=BaseModel)

REQUEST_TIMEOUT_S = 30


class AllModelsDown(Exception):
    """Raised when all primary and fallback LLM models fail or breakers are open."""


class LLMConfigError(Exception):
    """Raised when a provider is not configured (e.g. missing API key). Never retried."""


class LLMOutputError(Exception):
    """The model answered but not with parseable structured output (not a provider-health issue)."""


_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKER_LOCK = threading.Lock()


def get_breaker(label: str) -> CircuitBreaker:
    """Breakers are built from config.yaml `resilience` (threshold, window, cooldown)."""
    with _BREAKER_LOCK:
        if label not in _BREAKERS:
            cfg = get_settings().resilience
            _BREAKERS[label] = CircuitBreaker(
                label,
                failure_threshold=int(cfg.get("breaker_failures", 5)),
                cooldown_s=float(cfg.get("breaker_cooldown_s", 30.0)),
                window_s=float(cfg.get("breaker_window_s", 60.0)),
            )
        return _BREAKERS[label]


def reset_breakers() -> None:
    with _BREAKER_LOCK:
        _BREAKERS.clear()


def breaker_states() -> dict[str, str]:
    """Current state of each breaker (shown in the Gradio Ops tab)."""
    return {name: b.state for name, b in _BREAKERS.items()}


@lru_cache(maxsize=16)
def get_llm_for_tier(tier: str) -> BaseChatModel:
    """Client per tier. Timeout is bounded and provider-level retries are off:
    our own retry loop owns retries (jitter, Retry-After, breaker accounting)."""
    settings = get_settings()
    cfg = settings.models.get(tier, {})
    provider = cfg.get("provider", "groq")
    name = cfg.get("name", "llama-3.1-8b-instant")
    if provider == "openai":
        if not settings.fallback_api_key:
            raise LLMConfigError("FALLBACK_API_KEY is not set")
        return ChatOpenAI(model=name, api_key=settings.fallback_api_key, timeout=REQUEST_TIMEOUT_S, max_retries=0)  # type: ignore[arg-type]
    if not settings.groq_api_key:
        raise LLMConfigError("GROQ_API_KEY is not set")
    return ChatGroq(model=name, api_key=settings.groq_api_key, timeout=REQUEST_TIMEOUT_S, max_retries=0)  # type: ignore[arg-type]


def clear_llm_cache() -> None:
    get_llm_for_tier.cache_clear()


def _model_name(tier: str) -> str:
    return str(get_settings().models.get(tier, {}).get("name", tier))


def _invoke_once(agent: str, tier: str, label: str, messages: Any, schema: type[T], attempt: int) -> T:
    callbacks: list[Any] = [MeteringCallback(agent, _model_name(tier))]
    lf = langfuse_handler()
    if lf is not None:
        callbacks.append(lf)
    structured = get_llm_for_tier(tier).with_structured_output(schema, include_raw=True)
    out = structured.invoke(
        messages,
        config={
            "callbacks": callbacks,
            "tags": [agent, label],
            "metadata": {
                "agent": agent,
                "served_by": label,
                "attempt": attempt,
                "breaker_state": get_breaker(label).state,
                "request_id": current_request_id.get(),
            },
        },
    )
    parsed = out.get("parsed") if isinstance(out, dict) else out
    if parsed is None:
        raise LLMOutputError(f"unparseable structured output: {out.get('parsing_error') if isinstance(out, dict) else ''}")
    return cast(T, parsed)


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Seconds to wait before the next attempt: honour Retry-After, else full-jitter backoff."""
    cfg = get_settings().resilience
    retry_after = get_retry_after(exc)
    if retry_after is not None:
        return retry_after
    return calculate_backoff(attempt, float(cfg.get("base_s", 0.5)), float(cfg.get("cap_s", 8.0)))


def _call_provider(agent: str, tier: str, label: str, messages: Any, schema: type[T]) -> T | None:
    """Call one provider, retrying transient errors. Returns the result, or None to give up on it.

    Breaker accounting: success closes it; a permanent error or running out of retries counts as
    one failure; bad model output (LLMOutputError) is not a provider outage, so it does not count.
    """
    breaker = get_breaker(label)
    max_retries = int(get_settings().resilience.get("max_retries", 3))

    for attempt in range(max_retries):
        try:
            maybe_fail()  # chaos injection (no-op unless enabled)
            result = _invoke_once(agent, tier, label, messages, schema, attempt)
        except Exception as exc:  # noqa: BLE001 - classified below
            logger.warning(
                "llm_invocation_failed", agent=agent, provider=label, attempt=attempt,
                error=f"{type(exc).__name__}: {exc}",
            )
            if isinstance(exc, LLMOutputError):
                return None
            if not is_transient(exc):
                breaker.record_failure()
                return None
            if attempt == max_retries - 1:
                breaker.record_failure()
                return None
            time.sleep(_retry_delay(exc, attempt))
        else:
            breaker.record_success()
            return result
    return None


def resilient_invoke(agent: str, messages: Any, schema: type[T]) -> tuple[T, str]:
    """Call the model for ``agent``. Tries the primary tier, then the fallback model.

    Returns (result, label) where label is "primary" or "fallback". Raises AllModelsDown when
    both are unavailable (callers then use templates or refuse honestly).
    """
    primary_tier = get_settings().agent_tiers.get(agent, "small")
    for tier, label in ((primary_tier, "primary"), ("fallback", "fallback")):
        if not get_breaker(label).can_attempt():
            logger.warning("circuit_breaker_open_skipping", provider=label, agent=agent)
            continue
        result = _call_provider(agent, tier, label, messages, schema)
        if result is not None:
            return result, label

    raise AllModelsDown(f"All LLM models down or open for agent '{agent}'")
