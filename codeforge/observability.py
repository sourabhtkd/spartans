import re
import sys
from collections.abc import Iterator, MutableMapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import structlog

from codeforge.config import get_settings
from codeforge.privacy.pii import mask_obj


def mask_processor(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Last-mile processor that ensures all values in event_dict have PII masked."""
    masked: MutableMapping[str, Any] = mask_obj(dict(event_dict))
    return masked


_configured = False


def configure_observability() -> None:
    global _configured
    if _configured:
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            mask_processor,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=lambda *args: structlog.PrintLogger(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_logger(name: str = "codeforge") -> structlog.stdlib.BoundLogger:
    configure_observability()
    return structlog.get_logger(name)


# Request/thread id for the call currently being processed; read by metering and log binding.
current_request_id: ContextVar[str] = ContextVar("current_request_id", default="")
# Conversation (chat session) id for the turn being processed; becomes the Langfuse session id.
current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")

_langfuse_warned = False


def _langfuse_mask(*, data: Any, **kwargs: Any) -> Any:
    """Langfuse mask hook: scrub PII from everything sent to the tracing backend."""
    return mask_obj(data)


_langfuse_ready: bool | None = None  # None = not attempted yet


def _init_langfuse_v3() -> bool:
    """Create the (process-wide) Langfuse v3 client once; it owns a background exporter thread."""
    from langfuse import Langfuse

    settings = get_settings()
    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
        mask=_langfuse_mask,
    )
    sync_model_prices()
    return True


def sync_model_prices() -> int:
    """Register config.yaml model prices in Langfuse so it can compute cost per generation.

    Langfuse only prices models it knows; Groq models are not in its default table. Creates a
    model definition (exact, case-insensitive name match, USD per token) for each configured model
    that has none yet. Best-effort: returns how many were created, 0 on any failure.
    """
    from langfuse import get_client

    created = 0
    try:
        api = get_client().api
        existing: set[str] = set()
        page = 1
        while True:
            res = api.models.list(page=page, limit=100)
            existing.update(m.model_name for m in res.data)
            if page >= (res.meta.total_pages or 1):
                break
            page += 1
        for model in get_settings().models.values():
            if model.name in existing:
                continue
            api.models.create(
                model_name=model.name,
                match_pattern=f"(?i)^{re.escape(model.name)}$",
                unit="TOKENS",
                input_price=model.in_per_1k / 1000.0,
                output_price=model.out_per_1k / 1000.0,
            )
            existing.add(model.name)
            created += 1
    except Exception as exc:  # noqa: BLE001 - pricing sync must never break the app
        get_logger("observability").warning("langfuse_price_sync_failed", error=str(exc))
    return created


def flush_langfuse() -> None:
    """Send buffered traces now (short-lived processes may exit before the background export)."""
    client = get_langfuse_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001
        get_logger("observability").warning("langfuse_flush_failed", error=str(exc))


def has_langfuse_handler(callbacks: Any) -> bool:
    """True if a callbacks list / CallbackManager already carries a Langfuse handler."""
    handlers = getattr(callbacks, "handlers", callbacks) or []
    return any(type(h).__module__.startswith("langfuse") for h in handlers)


def run_facts(values: dict[str, Any], next_nodes: Any = ()) -> dict[str, Any]:
    """PII-free outcome facts of a graph run, attached to its Langfuse trace as metadata."""
    facts: dict[str, Any] = {
        "served_by": values.get("served_by"),
        "cache_hit": values.get("cache_hit"),
        "paused_at": list(next_nodes) or None,
    }
    policy = values.get("policy")
    if policy is not None:
        facts["policy_verdict"] = getattr(policy, "verdict", None)
    run = values.get("run_result")
    if run is not None:
        facts["exit_code"] = getattr(run, "exit_code", None)
        facts["timed_out"] = getattr(run, "timed_out", None)
    return {k: v for k, v in facts.items() if v is not None}


def langfuse_run_config(config: dict[str, Any], turn: "TurnTrace") -> dict[str, Any]:
    """Attach a Langfuse handler to a graph run config when the turn is being traced."""
    handler = langfuse_handler() if turn.trace_id else None
    if handler is not None:
        config["callbacks"] = [*config.get("callbacks", []), handler]
    return config


def langfuse_handler() -> Any:
    """Return a Langfuse LangChain CallbackHandler (v3, falling back to v2), or None.

    The v3 client is built once per process; only the cheap handler is created per call.
    Inputs/outputs are masked via Langfuse's ``mask`` hook. If keys are configured but the
    handler cannot be created, a warning is logged once; the pipeline is never interrupted.
    """
    global _langfuse_warned, _langfuse_ready
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        try:  # Langfuse v3
            from langfuse.langchain import CallbackHandler as HandlerV3

            if _langfuse_ready is None:
                _langfuse_ready = _init_langfuse_v3()
            return HandlerV3()
        except ImportError:  # Langfuse v2
            from langfuse.callback import CallbackHandler as HandlerV2  # type: ignore[import-not-found]

            return HandlerV2(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_base_url,
            )
    except Exception as exc:  # noqa: BLE001 - tracing must never break the pipeline
        if not _langfuse_warned:
            _langfuse_warned = True
            get_logger("observability").warning("langfuse_handler_unavailable", error=str(exc))
        return None


def get_langfuse_client() -> Any:
    """The process-wide Langfuse client, or None when tracing is not configured/available."""
    global _langfuse_ready
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        from langfuse import get_client

        if _langfuse_ready is None:
            _langfuse_ready = _init_langfuse_v3()
        return get_client()
    except Exception:  # noqa: BLE001 - tracing must never break the app
        return None


@dataclass
class TurnTrace:
    """Handle yielded by ``trace_turn``; ``trace_id`` is empty when Langfuse is off."""

    trace_id: str = ""
    output: Any = None
    metadata: dict[str, Any] | None = None


@contextmanager
def trace_turn(
    *, session_id: str, user_id: str, name: str, tags: list[str], input: Any
) -> Iterator[TurnTrace]:
    """Open ONE Langfuse trace for a chat turn (session = conversation, trace = turn).

    Everything traced inside (graph nodes, LLM calls) hangs under it, and ``session_id`` /
    ``user_id`` / ``tags`` propagate to every observation. A no-op without Langfuse keys, and a
    Langfuse failure never interrupts the turn. Set ``handle.output`` to record the reply.
    """
    handle = TurnTrace()
    stack = ExitStack()
    span: Any = None
    client = get_langfuse_client()
    if client is not None:
        try:
            from langfuse import propagate_attributes

            span = stack.enter_context(client.start_as_current_observation(name=name, as_type="span", input=input))
            stack.enter_context(
                propagate_attributes(session_id=session_id, user_id=user_id, tags=tags, trace_name=name)
            )
            handle.trace_id = client.get_current_trace_id() or ""
        except Exception as exc:  # noqa: BLE001
            get_logger("observability").warning("langfuse_trace_unavailable", error=str(exc))
            stack.close()
            span = None
    try:
        yield handle
    finally:
        try:
            if span is not None and (handle.output is not None or handle.metadata):
                span.update(output=handle.output, metadata=handle.metadata)
            stack.close()
        except Exception as exc:  # noqa: BLE001
            get_logger("observability").warning("langfuse_trace_close_failed", error=str(exc))


def score_trace(trace_id: str, name: str, value: float, comment: str = "") -> bool:
    """Attach a score (e.g. user feedback) to a Langfuse trace. Best-effort; False when not sent."""
    client = get_langfuse_client()
    if client is None or not trace_id:
        return False
    try:
        client.create_score(trace_id=trace_id, name=name, value=value, data_type="NUMERIC", comment=comment or None)
        return True
    except Exception as exc:  # noqa: BLE001
        get_logger("observability").warning("langfuse_score_failed", error=str(exc))
        return False
