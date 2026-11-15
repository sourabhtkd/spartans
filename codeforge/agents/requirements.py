from pathlib import Path
from typing import Any

from codeforge.agents.context import load_prompt, request_with_context
from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.state import CodeForgeState, Requirements, worst_tier

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "requirements.md"


class RequirementsAgent:
    def __init__(self) -> None:
        self.system_prompt = load_prompt("requirements")

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        req_text = state.get("request_masked", "")
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": request_with_context(state)},
        ]
        try:
            requirements, tier = resilient_invoke("requirements", messages, Requirements)
            return {"requirements": requirements, "served_by": worst_tier(state.get("served_by"), tier)}
        except AllModelsDown:
            from codeforge.resilience.templates import match_template

            tpl = match_template(req_text)
            fn_name = tpl.name if tpl else "solve"
            return {
                "requirements": Requirements(function_name=fn_name, description=fn_name),
                "served_by": worst_tier(state.get("served_by"), "template" if tpl else "refused"),
            }
