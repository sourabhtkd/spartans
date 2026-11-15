"""Golden checks, grouped by theme. CHECKS maps a golden-case `kind` to the function that verifies it."""
from typing import Any

from evals.checks import hitl, privacy, quality, resilience, safety

CHECKS: dict[str, Any] = {
    **privacy.CHECKS,
    **safety.CHECKS,
    **hitl.CHECKS,
    **resilience.CHECKS,
    **quality.CHECKS,
}
