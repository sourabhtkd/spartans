"""Evaluator agent: an advisory second opinion on the generated code (never decides pass/fail)."""
from typing import Any

from pydantic import BaseModel, Field

from codeforge.agents.base import BaseAgent
from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.privacy.pii import mask_obj
from codeforge.state import CodeForgeState


class EvaluationCritique(BaseModel):
    clarity_score: float = 1.0
    advisory_notes: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class EvaluatorAgent(BaseAgent):
    """Advisory reviewer. Its notes appear in the report but NEVER change pass/fail:
    status comes only from the real pytest exit code."""

    name = "evaluator"

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        events = list(state.get("events", []))
        reqs = state.get("requirements")
        user = (
            f"Requirements:\n{reqs.model_dump_json(indent=2) if reqs else '(none)'}\n\n"
            f"Code:\n```python\n{state.get('code', '')}\n```\n\n"
            f"Tests:\n```python\n{state.get('tests', '')}\n```\n"
        )
        try:
            critique, tier = resilient_invoke(self.name, self.messages(user), EvaluationCritique)
            events.append(
                {"stage": "evaluator_advisory", "source": "model", "critique": mask_obj(critique.model_dump())}
            )
            return {"events": events, "served_by": self.served_by(state, tier)}
        except AllModelsDown:
            # No model available: say so plainly instead of inventing a review.
            critique = EvaluationCritique(
                clarity_score=0.0,
                advisory_notes=["Automated review unavailable (model unreachable); results above come from the real test run."],
            )
            events.append({"stage": "evaluator_advisory", "source": "unavailable", "critique": critique.model_dump()})
            return {"events": events}
