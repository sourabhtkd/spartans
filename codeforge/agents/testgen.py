from pathlib import Path
from typing import Any

from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.state import CodeForgeState, GeneratedTests, merge_tier

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "testgen.md"


class TestGenAgent:
    __test__ = False

    def __init__(self) -> None:
        self.system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        reqs = state.get("requirements")
        fn_name = reqs.function_name if reqs else "solve"
        user_content = (
            f"Requirements:\n{reqs.model_dump_json(indent=2) if reqs else 'Write tests'}\n"
            f"Ensure tests import: from solution import {fn_name}\n"
        )
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            generated, tier = resilient_invoke("testgen", messages, GeneratedTests)
            return {"tests": generated.tests, "served_tier": merge_tier(state.get("served_tier"), tier)}
        except AllModelsDown:
            from codeforge.resilience.templates import match_template

            search_text = f"{fn_name} {state.get('raw_request_masked', '')}"
            tpl = match_template(search_text)
            if tpl:
                return {"tests": tpl.tests, "served_tier": merge_tier(state.get("served_tier"), "template")}
            return {"tests": "", "served_tier": merge_tier(state.get("served_tier"), "refused")}
