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

from codeforge.agents.evaluator import EvaluationCritique
from codeforge.resilience.templates import match_template
from codeforge.state import (
    GeneratedCode,
    GeneratedTests,
    HistorySummary,
    Requirements,
    RouteDecision,
)


def _content(message: Any) -> str:
    return str(message["content"] if isinstance(message, dict) else getattr(message, "content", message))


def _text(messages: Any) -> str:
    return "\n".join(_content(m) for m in messages)


def _function_name_from_prompt(prompt: str) -> str:
    """Read `function_name` from the 'Requirements:' JSON block of a CodeGen/TestGen prompt."""
    try:
        requirements, _ = json.JSONDecoder().raw_decode(prompt.split("Requirements:\n", 1)[1])
        return str(requirements["function_name"])
    except (IndexError, ValueError, KeyError, TypeError):
        return ""


class _SimStructured:
    def __init__(self, schema: type) -> None:
        self.schema = schema

    def invoke(self, messages: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
        user = _content(messages[-1])
        parsed = self._answer(user)
        out_text = parsed.model_dump_json() if parsed is not None else ""
        in_tokens = max(1, len(_text(messages)) // 4)
        out_tokens = max(1, len(out_text) // 4)
        callbacks = (config or {}).get("callbacks") or []
        for cb in getattr(callbacks, "handlers", callbacks):  # list or CallbackManager
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
        """Build the structured answer for this schema, or None when the simulator cannot answer."""
        if self.schema is EvaluationCritique:
            return EvaluationCritique(
                clarity_score=0.0,
                advisory_notes=["Simulated provider: no real code review was performed."],
            )
        if self.schema is HistorySummary:
            return HistorySummary(summary=user[:200])
        if self.schema is RouteDecision:
            return RouteDecision(kind="python_function", language="python", confidence=0.9)
        if self.schema is Requirements:
            template = match_template(user)
            return Requirements(function_name=template.name if template else "solve", description=user)

        # CodeGen / TestGen prompts embed the Requirements JSON; answer from the matching template.
        template = match_template(_function_name_from_prompt(user) or user)
        if template is None:
            return None
        if self.schema is GeneratedCode:
            return GeneratedCode(code=template.code)
        if self.schema is GeneratedTests:
            return GeneratedTests(tests=template.tests)
        return None


class SimulatedLLM:
    def with_structured_output(self, schema: type, include_raw: bool = False) -> _SimStructured:
        return _SimStructured(schema)
