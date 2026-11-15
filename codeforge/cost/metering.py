import time
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from codeforge.config import get_settings
from codeforge.observability import get_logger
from codeforge.store.db import Metric, init_db

logger = get_logger("metering")


class MeteringCallback(BaseCallbackHandler):
    """LangChain callback handler that records per-agent tokens, latency, and cost to the DB."""

    def __init__(self, agent_name: str, model_name: str = "llama-3.1-8b-instant") -> None:
        super().__init__()
        self.agent_name = agent_name
        self.model_name = model_name
        self.start_time: float = 0.0

    def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any
    ) -> None:
        self.start_time = time.time()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        latency_s = time.time() - self.start_time
        input_tokens = 0
        output_tokens = 0

        # Try extract token usage from response llm_output
        if response.llm_output and "token_usage" in response.llm_output:
            usage = response.llm_output["token_usage"]
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
        else:
            # Fallback estimation: 1 token ~ 4 characters
            for gen_list in response.generations:
                for gen in gen_list:
                    output_tokens += max(1, len(gen.text) // 4)

        settings = get_settings()
        cost_usd = 0.0
        # Compute cost from model config
        for m_cfg in settings.models.values():
            if m_cfg.get("name") == self.model_name:
                in_rate = m_cfg.get("in_per_1k", 0.0) / 1000.0
                out_rate = m_cfg.get("out_per_1k", 0.0) / 1000.0
                cost_usd = (input_tokens * in_rate) + (output_tokens * out_rate)
                break

        # Persist metric
        try:
            sf = init_db()
            with sf() as session:
                rec = Metric(
                    agent=self.agent_name,
                    model=self.model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_s=latency_s,
                    cost_usd=cost_usd,
                )
                session.add(rec)
                session.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("metering_write_failed", error=str(e))
