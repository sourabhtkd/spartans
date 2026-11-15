"""CodeGen agent: writes the solution from the requirements, and repairs it when tests fail."""
from typing import Any

from codeforge.agents.base import BaseAgent
from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.resilience.templates import match_template
from codeforge.state import CodeForgeState, GeneratedCode


def repair_instructions(state: CodeForgeState, attempts: int) -> str:
    """Extra prompt text for a repair attempt: the previous code and why its tests failed."""
    run_result = state.get("run_result")
    failure = (run_result.last_failing_test or run_result.stdout) if run_result else ""
    return (
        f"\nREPAIR ATTEMPT #{attempts + 1}:\n"
        f"Previous Code:\n```python\n{state.get('code', '')}\n```\n"
        f"Failing Test / Error:\n{failure or 'Assertion failed'}\n"
        "Please fix the implementation to satisfy all test cases.\n"
    )


class CodeGenAgent(BaseAgent):
    name = "codegen"

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        requirements = state.get("requirements")
        attempts = state.get("attempts", 0)

        spec = requirements.model_dump_json(indent=2) if requirements else "Implement requested function"
        user_content = f"Requirements:\n{spec}\n"
        if attempts > 0 and state.get("code"):
            user_content += repair_instructions(state, attempts)

        try:
            generated, tier = resilient_invoke(self.name, self.messages(user_content), GeneratedCode)
            return {"code": generated.code, "attempts": attempts + 1, "served_by": self.served_by(state, tier)}
        except AllModelsDown:
            function_name = requirements.function_name if requirements else ""
            template = match_template(f"{function_name} {state.get('request_masked', '')}")
            if template:
                return {"code": template.code, "attempts": attempts + 1, "served_by": self.served_by(state, "template")}
            return {"code": "", "attempts": attempts + 1, "served_by": self.served_by(state, "refused")}
