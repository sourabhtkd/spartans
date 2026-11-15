from pathlib import Path
from typing import Any

from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.state import CodeForgeState, Requirements, merge_tier

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "requirements.md"


class RequirementsAgent:
    def __init__(self) -> None:
        self.system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        req_text = state.get("raw_request_masked", "")
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": req_text},
        ]
        try:
            requirements, tier = resilient_invoke("requirements", messages, Requirements)
            return {"requirements": requirements, "served_tier": merge_tier(state.get("served_tier"), tier)}
        except AllModelsDown:
            from codeforge.resilience.templates import match_template

            tpl = match_template(req_text)
            fn_name = tpl.name if tpl else "solve"
            return {
                "requirements": Requirements(function_name=fn_name, description=fn_name),
                "served_tier": merge_tier(state.get("served_tier"), "template" if tpl else "refused"),
            }
