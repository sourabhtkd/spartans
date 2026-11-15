"""Structured logging around every graph node (start / end / paused / error, with duration)."""
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

import structlog
from langgraph.errors import GraphInterrupt

from codeforge.observability import get_logger
from codeforge.state import CodeForgeState

logger = get_logger("pipeline")

# Optional callback told the node name each time a node starts (the chat UI shows it as live progress).
step_listener: ContextVar[Callable[[str], None] | None] = ContextVar("step_listener", default=None)


def node_facts(output: dict[str, Any]) -> dict[str, Any]:
    """Small, PII-free facts about a node's output, added to its node_end log line."""
    facts: dict[str, Any] = {}
    if output.get("policy") is not None:
        facts["policy_verdict"] = output["policy"].verdict
    if "served_by" in output:
        facts["served_by"] = output["served_by"]
    if "cache_hit" in output:
        facts["cache_hit"] = output["cache_hit"]
    if output.get("run_result") is not None:
        facts["exit_code"] = output["run_result"].exit_code
        facts["timed_out"] = output["run_result"].timed_out
    if output.get("ticket") is not None:
        facts["ticket_priority"] = output["ticket"].priority
    return facts


def traced_node(name: str, node: Callable[[CodeForgeState], Any]) -> Any:
    """Wrap a node so each run logs node_start, then node_end / node_paused / node_error."""

    def run(state: CodeForgeState) -> Any:
        structlog.contextvars.bind_contextvars(agent=name)
        logger.info("node_start", node=name)
        listener = step_listener.get()
        if listener is not None:
            try:
                listener(name)
            except Exception as exc:  # noqa: BLE001 - UI progress must never break a node
                logger.debug("step_listener_failed", node=name, error=str(exc))
        started = time.perf_counter()

        def elapsed_ms() -> float:
            return round((time.perf_counter() - started) * 1000, 1)

        try:
            output = node(state)
        except Exception as exc:  # GraphInterrupt (waiting for a human) is an Exception subclass
            event = "node_paused" if isinstance(exc, GraphInterrupt) else "node_error"
            logger.info(event, node=name, duration_ms=elapsed_ms())
            raise
        facts = node_facts(output) if isinstance(output, dict) else {}
        logger.info("node_end", node=name, duration_ms=elapsed_ms(), **facts)
        return output

    run.__name__ = name
    return run
