"""Requirements agent: turns the request into a structured spec (function name, parameters, edge cases)."""
from typing import Any

from codeforge.agents.base import BaseAgent, request_with_context
from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.resilience.templates import match_template
from codeforge.state import CodeForgeState, Requirements


class RequirementsAgent(BaseAgent):
    name = "requirements"

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        messages = self.messages(request_with_context(state))
        try:
            requirements, tier = resilient_invoke(self.name, messages, Requirements)
            return {"requirements": requirements, "served_by": self.served_by(state, tier)}
        except AllModelsDown:
            template = match_template(state.get("request_masked", ""))
            name = template.name if template else "solve"
            return {
                "requirements": Requirements(function_name=name, description=name),
                "served_by": self.served_by(state, "template" if template else "refused"),
            }
