import sys
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

from codeforge.config import get_settings
from codeforge.guard.pii import mask_obj


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
