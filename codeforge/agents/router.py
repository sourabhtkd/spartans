from pathlib import Path
from typing import Any, Literal

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
            {"role": "user", "content": req_text},
        ]
        try:
            decision, tier = resilient_invoke("router", messages, RouteDecision)
            return {"route": decision, "served_tier": merge_tier(state.get("served_tier"), tier)}
        except AllModelsDown:  # fallback to keyword router on model outage
            # Plain Python keyword routing during outages
            lowered = req_text.lower()
            kind: Literal["python_function", "sql", "other"]
            if any(k in lowered for k in ["select", "insert", "sql", "from", "table"]):
                kind = "sql"
            elif any(k in lowered for k in ["function", "python", "def", "code", "write", "palindrome", "factorial"]):
                kind = "python_function"
            else:
                kind = "python_function"
            default_route = RouteDecision(
                kind=kind, language="python" if kind == "python_function" else "sql", confidence=0.7
            )
            return {"route": default_route, "served_tier": merge_tier(state.get("served_tier"), "template")}
