"""A SIMULATED model provider for running benchmarks without an API key.

It is deliberately dumb: it answers only tasks that match a template, using the template
code/tests, and reports token usage estimated from text length (~4 chars/token). The real
resilience stack (chaos injection, retries, breakers, fallback, metering) runs unchanged on
top of it, so chaos results are genuine for CodeForge's logic but say nothing about a real
provider's quality. Every report produced with it is labelled SIMULATED.
"""
import json
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from codeforge.resilience.templates import match_template
from codeforge.state import GeneratedCode, GeneratedTests, Requirements, RouteDecision


def _text(messages: Any) -> str:
    parts = []
    for m in messages:
        parts.append(m["content"] if isinstance(m, dict) else str(getattr(m, "content", m)))
    return "\n".join(parts)


class _SimStructured:
    def __init__(self, schema: type) -> None:
        self.schema = schema

    def invoke(self, messages: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
        user = messages[-1]["content"] if isinstance(messages[-1], dict) else str(messages[-1])
        parsed = self._answer(user)
        out_text = parsed.model_dump_json() if parsed is not None else ""
        in_tokens = max(1, len(_text(messages)) // 4)
        out_tokens = max(1, len(out_text) // 4)
        for cb in (config or {}).get("callbacks", []):
            msg = AIMessage(
                content=out_text,
                usage_metadata={
                    "input_tokens": in_tokens,
                    "output_tokens": out_tokens,
                    "total_tokens": in_tokens + out_tokens,
                },
            )
            if hasattr(cb, "on_chat_model_start"):
                cb.on_chat_model_start({}, [[]])
            cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]))
        if parsed is None:
            return {"parsed": None, "raw": None, "parsing_error": "simulated provider cannot answer"}
        return {"parsed": parsed, "raw": None, "parsing_error": None}

    def _answer(self, user: str) -> Any:
        if self.schema is RouteDecision:
            return RouteDecision(kind="python_function", language="python", confidence=0.9)
        if self.schema is Requirements:
            tpl = match_template(user)
            return Requirements(function_name=tpl.name if tpl else "solve", description=user)
        # codegen / testgen messages embed the Requirements JSON
        try:
            name = json.JSONDecoder().raw_decode(user.split("Requirements:\n", 1)[1])[0]["function_name"]
        except (IndexError, ValueError, KeyError, TypeError):
            name = ""
        tpl = match_template(name or user)
        if tpl is None:
            return None
        if self.schema is GeneratedCode:
            return GeneratedCode(code=tpl.code)
        if self.schema is GeneratedTests:
            return GeneratedTests(tests=tpl.tests)
        return None


class SimulatedLLM:
    def with_structured_output(self, schema: type, include_raw: bool = False) -> _SimStructured:
        return _SimStructured(schema)


def install_simulated_provider(monkeypatch_target: Any) -> None:
    """Replace the real client factory with the simulated one (module ``codeforge.llm``)."""
    monkeypatch_target.get_llm_for_tier = lambda tier: SimulatedLLM()
