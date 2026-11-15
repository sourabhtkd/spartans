import random
import re
from typing import Any


class TransientError(Exception):
    """Raised for transient service or network errors that can be retried."""


class NonTransientError(Exception):
    """Raised for permanent client or validation errors that must fail fast."""


_TRANSIENT_CLASS_NAMES = {
    "ratelimiterror", "apitimeouterror", "apiconnectionerror", "internalservererror",
    "timeoutexception", "readtimeout", "connecttimeout", "timeouterror", "connectionerror",
    "transienterror", "serviceunavailableerror", "connectionreseterror", "remoteprotocolerror",
}
_PERMANENT_CLASS_NAMES = {
    "authenticationerror", "permissiondeniederror", "badrequesterror", "notfounderror",
    "unprocessableentityerror", "validationerror", "syntaxerror", "nontransienterror",
    "llmconfigerror",
}
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
# Status codes in messages only count in recognisable positions ("Error code: 429", "HTTP 503",
# or a leading code) so text like "failed on 500 rows" is not misread as a server error.
_STATUS_IN_MESSAGE = re.compile(
    r"(?:^\s*|error code:?\s*|status(?: code)?:?\s*|http\s*)(429|5\d\d)\b", re.IGNORECASE
)
_TRANSIENT_KEYWORDS = (
    "rate limit", "timed out", "timeout", "connection reset", "connection error",
    "service unavailable", "overloaded",
)


def is_transient(exc: BaseException) -> bool:
    """Classify an exception as transient (retry) vs permanent (fail fast).

    Order: exception class -> status_code attribute -> message patterns.
    Transient: 429, 500/502/503/504, timeouts, connection resets.
    Permanent: auth, bad request, validation, config errors, any other 4xx.
    """
    names = {c.__name__.lower() for c in type(exc).__mro__}
    if names & _PERMANENT_CLASS_NAMES:
        return False
    if names & _TRANSIENT_CLASS_NAMES:
        return True

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        if status_code in _TRANSIENT_STATUS:
            return True
        if 400 <= status_code < 500:
            return False

    msg = str(exc)
    low = msg.lower()
    if any(k in low for k in ("401", "unauthorized", "invalid_api_key", "forbidden", "403")):
        return False
    if _STATUS_IN_MESSAGE.search(msg):
        return True
    return any(k in low for k in _TRANSIENT_KEYWORDS)


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
