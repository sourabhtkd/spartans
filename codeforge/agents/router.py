import re
from pathlib import Path
from typing import Any, Literal

from codeforge.agents.context import request_with_context
from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.state import CodeForgeState, RouteDecision, merge_tier

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "router.md"


class RouterAgent:
    def __init__(self) -> None:
        self.system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        req_text = state.get("raw_request_masked", "")
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": request_with_context(state)},
        ]
        try:
            decision, tier = resilient_invoke("router", messages, RouteDecision)
            return {"route": decision, "served_tier": merge_tier(state.get("served_tier"), tier)}
        except AllModelsDown:  # fallback to keyword router on model outage
            # Plain-Python keyword routing during outages: SQL signals, clearly-non-code signals, else python.
            lowered = req_text.lower()
            kind: Literal["python_function", "sql", "other"]
            if re.search(r"\bsql\b|\bselect\b.+\bfrom\b|\bjoin\b|\b(?:query|queries)\b.*\btable\b", lowered):
                kind = "sql"
            elif re.search(
                r"\b(?:poem|story|essay|joke|recipe|weather|translate|capital of|who is|what is the)\b", lowered
            ):
                kind = "other"  # clearly not a code request
            else:
                # Default to python: with models down we cannot know better, and the pipeline then
                # answers from templates or refuses honestly (with a ticket) rather than guessing.
                kind = "python_function"
            default_route = RouteDecision(
                kind=kind, language={"python_function": "python", "sql": "sql", "other": "none"}[kind], confidence=0.7
            )
            return {"route": default_route, "served_tier": merge_tier(state.get("served_tier"), "template")}
