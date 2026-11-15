"""TestGen agent: writes pytest tests for the requirements."""
from typing import Any

from codeforge.agents.base import BaseAgent
from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.resilience.templates import match_template
from codeforge.state import CodeForgeState, GeneratedTests


class TestGenAgent(BaseAgent):
    __test__ = False  # not a pytest test class, despite the name
    name = "testgen"

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        requirements = state.get("requirements")
        function_name = requirements.function_name if requirements else "solve"
        spec = requirements.model_dump_json(indent=2) if requirements else "Write tests"
        user_content = f"Requirements:\n{spec}\nEnsure tests import: from solution import {function_name}\n"

        try:
            generated, tier = resilient_invoke(self.name, self.messages(user_content), GeneratedTests)
            return {"tests": generated.tests, "served_by": self.served_by(state, tier)}
        except AllModelsDown:
            template = match_template(f"{function_name} {state.get('request_masked', '')}")
            if template:
                return {"tests": template.tests, "served_by": self.served_by(state, "template")}
            return {"tests": "", "served_by": self.served_by(state, "refused")}
