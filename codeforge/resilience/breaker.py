import time
from collections.abc import Callable
from typing import Literal


class CircuitBreaker:
    """Three-state circuit breaker (CLOSED, OPEN, HALF_OPEN) with injectable clock.
    Protects downstream LLM providers from failure storms and cuts latency on outage.
    """

    def __init__(
        self,
        name: str = "primary",
        failure_threshold: int = 5,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.time,
        window_s: float | None = None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.clock = clock
        # Only failures within the last `window_s` seconds count toward the threshold.
        self.window_s = window_s
        self._failure_times: list[float] = []

        self.state: Literal["CLOSED", "OPEN", "HALF_OPEN"] = "CLOSED"
        self.failure_count = 0
        self.last_failure_time = 0.0
        self._half_open_probe_in_flight = False

    def can_attempt(self) -> bool:
        """Determines if a request can be dispatched to this provider."""
        now = self.clock()
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if now - self.last_failure_time >= self.cooldown_s:
                self.state = "HALF_OPEN"
                self._half_open_probe_in_flight = True
                return True
            return False
        if self.state == "HALF_OPEN":
            if not self._half_open_probe_in_flight:
                self._half_open_probe_in_flight = True
                return True
            return False
        return False

    def record_success(self) -> None:
        """Records a successful response; closes breaker if previously half-open."""
        self.failure_count = 0
        self._failure_times.clear()
        self.state = "CLOSED"
        self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        """Records an execution failure; trips breaker to OPEN if threshold exceeded."""
        now = self.clock()
        self._failure_times.append(now)
        if self.window_s is not None:
            self._failure_times = [t for t in self._failure_times if now - t <= self.window_s]
        self.failure_count = len(self._failure_times)
        self.last_failure_time = now
        if self.state == "HALF_OPEN" or self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            self._half_open_probe_in_flight = False
