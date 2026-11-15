import time
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from codeforge.config import get_settings
from codeforge.observability import current_request_id, get_logger
from codeforge.store.db import Metric, init_db

logger = get_logger("metering")


def compute_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Cost in USD from the per-1k price table in config.yaml."""
    for cfg in get_settings().models.values():
        if cfg.get("name") == model_name:
            return (
                input_tokens * cfg.get("in_per_1k", 0.0) + output_tokens * cfg.get("out_per_1k", 0.0)
            ) / 1000.0
    return 0.0


def _extract_usage(response: LLMResult) -> tuple[int, int]:
    """Real token counts: message.usage_metadata first, then llm_output, else a char estimate."""
    for gen_list in response.generations:
        for gen in gen_list:
            usage = getattr(getattr(gen, "message", None), "usage_metadata", None)
            if usage:
                return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    out = response.llm_output or {}
    usage = out.get("token_usage") or {}
    if usage:
        return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    est = sum(max(1, len(g.text) // 4) for gl in response.generations for g in gl)
    return 0, est


class MeteringCallback(BaseCallbackHandler):
    """Records per-agent tokens, latency and cost into the `metrics` table."""

    def __init__(self, agent_name: str, model_name: str, request_id: str = "") -> None:
        super().__init__()
        self.agent_name = agent_name
        self.model_name = model_name
        self.request_id = request_id or current_request_id.get()
        self.start_time = time.time()

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        self.start_time = time.time()

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        self.start_time = time.time()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        latency_s = time.time() - self.start_time
        input_tokens, output_tokens = _extract_usage(response)
        try:
            with init_db()() as session:
                session.add(
                    Metric(
                        request_id=self.request_id,
                        agent=self.agent_name,
                        model=self.model_name,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_s=latency_s,
                        cost_usd=compute_cost(self.model_name, input_tokens, output_tokens),
                    )
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 - metering must never break a request
            logger.warning("metering_write_failed", error=str(exc))
