import sys
from collections.abc import MutableMapping
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


def langfuse_handler() -> Any:
    """Returns a Langfuse CallbackHandler if API keys are set, otherwise None."""
    settings = get_settings()
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        try:
            from langfuse.callback import CallbackHandler  # type: ignore

            return CallbackHandler(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
        except Exception:  # noqa: BLE001 - optional import / init failure must not crash pipeline
            return None
    return None
