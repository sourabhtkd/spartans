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
        host=settings.langfuse_host,
        mask=_langfuse_mask,
    )
    return True


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
                host=settings.langfuse_host,
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
            if span is not None and handle.output is not None:
                span.update(output=handle.output)
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
