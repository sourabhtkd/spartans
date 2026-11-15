from pathlib import Path
from typing import Any

from codeforge.agents.context import load_prompt
from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.state import CodeForgeState, GeneratedCode, merge_tier

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "codegen.md"


class CodeGenAgent:
    def __init__(self) -> None:
        self.system_prompt = load_prompt("codegen")

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        reqs = state.get("requirements")
        attempts = state.get("attempts", 0)
        previous_code = state.get("code", "")
        run_result = state.get("run_result")
        last_failing_test = run_result.last_failing_test if run_result else None

        user_content = f"Requirements:\n{reqs.model_dump_json(indent=2) if reqs else 'Implement requested function'}\n"
        if attempts > 0 and previous_code:
            user_content += (
                f"\nREPAIR ATTEMPT #{attempts + 1}:\n"
                f"Previous Code:\n```python\n{previous_code}\n```\n"
                f"Failing Test / Error:\n{last_failing_test or (run_result.stdout if run_result else '') or 'Assertion failed'}\n"
                "Please fix the implementation to satisfy all test cases.\n"
            )

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            generated, tier = resilient_invoke("codegen", messages, GeneratedCode)
            return {
                "code": generated.code,
                "attempts": attempts + 1,
                "served_tier": merge_tier(state.get("served_tier"), tier),
            }
        except AllModelsDown:
            from codeforge.resilience.templates import match_template

            search_text = f"{reqs.function_name if reqs else ''} {state.get('raw_request_masked', '')}"
            tpl = match_template(search_text)
            if tpl:
                return {
                    "code": tpl.code,
                    "attempts": attempts + 1,
                    "served_tier": merge_tier(state.get("served_tier"), "template"),
                }
            return {
                "code": "",
                "attempts": attempts + 1,
                "served_tier": merge_tier(state.get("served_tier"), "refused"),
            }
