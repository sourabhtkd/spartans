import random
import re
from typing import Any


class TransientError(Exception):
    """Raised for transient service or network errors that can be retried."""


class NonTransientError(Exception):
    """Raised for permanent client or validation errors that must fail fast."""


def is_transient(exc: Exception) -> bool:
    """Classifies exceptions as transient (retryable) vs permanent (fail fast).
    Transient: HTTP 429, 500, 502, 503, 504, connection reset, timeout.
    Permanent: 400, 401 (auth), 403, 404, schema validation errors.
    """
    exc_str = str(exc).lower()
    exc_type = type(exc).__name__.lower()

    # Permanent error checks
    if any(k in exc_str or k in exc_type for k in ["401", "unauthorized", "invalid_api_key", "forbidden", "403"]):
        return False
    if "validationerror" in exc_type or "syntaxerror" in exc_type:
        return False

    # Transient error checks
    transient_indicators = [
        "429",
        "rate limit",
        "500",
        "502",
        "503",
        "504",
        "timeout",
        "timed out",
        "connection reset",
        "connection error",
        "service unavailable",
        "overloaded",
    ]
    if any(k in exc_str or k in exc_type for k in transient_indicators):
        return True

    # Check status_code attribute if present
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        if status_code in (429, 500, 502, 503, 504):
            return True
        if 400 <= status_code < 500:
            return False

    return False


def get_retry_after(exc: Exception) -> float | None:
    """Extracts Retry-After header seconds if present on the exception."""
    headers = getattr(exc, "headers", None)
    if headers and hasattr(headers, "get"):
        val = headers.get("retry-after")
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    # Check regex on message
    m = re.search(r"retry[- ]after[:\s]+(\d+(?:\.\d+)?)", str(exc), re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def calculate_backoff(
    attempt: int,
    base_s: float = 0.5,
    cap_s: float = 8.0,
    rng: Any = None,
) -> float:
    """Full-jitter exponential backoff: sleep = uniform(0, min(cap, base * 2 ** attempt))."""
    cap = min(cap_s, base_s * (2**attempt))
    if rng:
        return rng.uniform(0.0, cap)
    return random.uniform(0.0, cap)
