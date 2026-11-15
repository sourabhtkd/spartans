import random
from typing import Any

from codeforge.config import get_settings
from codeforge.resilience.retry import TransientError


def maybe_fail(rng: Any = None) -> None:
    """Injects synthetic transient chaos (429, 503, Timeout) before making an LLM call.
    Controlled by CHAOS_FAILURE_RATE (0.0 to 1.0) and TOTAL_OUTAGE boolean.
    """
    settings = get_settings()

    if settings.total_outage:
        raise TransientError("Synthetic 503 Total Outage: all providers offline")

    rate = settings.chaos_failure_rate
    if rate <= 0.0:
        return

    rand_val = rng.random() if rng else random.random()
    if rand_val < rate:
        choice = rng.choice if rng else random.choice
        err_type = choice(["429 Too Many Requests", "503 Service Unavailable", "Timeout"])
        raise TransientError(f"Synthetic Chaos Error: {err_type}")
