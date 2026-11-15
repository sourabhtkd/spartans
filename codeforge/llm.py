import time
from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from codeforge.config import get_settings
from codeforge.observability import get_logger
from codeforge.resilience.breaker import CircuitBreaker
from codeforge.resilience.chaos import maybe_fail
from codeforge.resilience.retry import (
    calculate_backoff,
    get_retry_after,
    is_transient,
)

logger = get_logger("llm")

T = TypeVar("T", bound=BaseModel)


class AllModelsDown(Exception):
    """Raised when all primary and fallback LLM models fail or breakers are open."""


_primary_breaker = CircuitBreaker("primary", failure_threshold=5, cooldown_s=30.0)
_fallback_breaker = CircuitBreaker("fallback", failure_threshold=5, cooldown_s=30.0)


def get_llm_for_tier(tier: str) -> BaseChatModel:
    settings = get_settings()
    model_cfg = settings.models.get(tier, {})
    provider = model_cfg.get("provider", "groq")
    model_name = model_cfg.get("name", "llama-3.1-8b-instant")
    if provider == "openai":
        return ChatOpenAI(
            model=model_name,
            api_key=settings.fallback_api_key or "sk-dummy",  # type: ignore[arg-type]
        )
    return ChatGroq(
        model=model_name,
        api_key=settings.groq_api_key or "gsk_dummy",  # type: ignore[arg-type]
    )


def resilient_invoke(agent: str, messages: Any, schema: type[T]) -> tuple[T, str]:
    """Single resilient call path for all LLM calls.
    Tiers: primary -> fallback -> AllModelsDown.
    Includes chaos injection, exponential full-jitter retries, and circuit breakers.
    """
    settings = get_settings()
    primary_tier = settings.agent_tiers.get(agent, "small")
    fallback_tier = "fallback"

    tiers_to_try = [
        (primary_tier, _primary_breaker, "primary"),
        (fallback_tier, _fallback_breaker, "fallback"),
    ]

    max_retries = settings.resilience.get("max_retries", 3)
    base_s = settings.resilience.get("base_s", 0.5)
    cap_s = settings.resilience.get("cap_s", 8.0)

    for tier_name, breaker, label in tiers_to_try:
        if not breaker.can_attempt():
            logger.warning("circuit_breaker_open_skipping", provider=label)
            continue

        for attempt in range(max_retries):
            try:
                # 1. Chaos injection
                maybe_fail()

                # 2. Invoke structured model
                llm = get_llm_for_tier(tier_name)
                structured = llm.with_structured_output(schema)
                result = structured.invoke(messages)

                breaker.record_success()
                return result, label  # type: ignore[return-value]

            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "llm_invocation_failed",
                    agent=agent,
                    provider=label,
                    attempt=attempt,
                    error=str(exc),
                )
                if not is_transient(exc):
                    breaker.record_failure()
                    break

                if attempt == max_retries - 1:
                    breaker.record_failure()
                else:
                    retry_after = get_retry_after(exc)
                    delay = retry_after if retry_after is not None else calculate_backoff(attempt, base_s, cap_s)
                    time.sleep(delay)

    raise AllModelsDown(f"All LLM models down or open for agent '{agent}'")
