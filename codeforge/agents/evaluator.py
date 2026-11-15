from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeforge.agents.context import load_prompt
from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.privacy.pii import mask_obj
from codeforge.state import CodeForgeState, worst_tier

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "evaluator.md"


class EvaluationCritique(BaseModel):
    clarity_score: float = 1.0
    advisory_notes: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class EvaluatorAgent:
    """Advisory reviewer. Its notes appear in the report but NEVER change pass/fail:
    status comes only from the real pytest exit code."""

    def __init__(self) -> None:
        self.system_prompt = load_prompt("evaluator")

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        events = list(state.get("events", []))
        reqs = state.get("requirements")
        user = (
            f"Requirements:\n{reqs.model_dump_json(indent=2) if reqs else '(none)'}\n\n"
            f"Code:\n```python\n{state.get('code', '')}\n```\n\n"
            f"Tests:\n```python\n{state.get('tests', '')}\n```\n"
        )
        messages = [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": user}]
        try:
            critique, tier = resilient_invoke("evaluator", messages, EvaluationCritique)
            source = "model"
            events.append({"stage": "evaluator_advisory", "source": source, "critique": mask_obj(critique.model_dump())})
            return {"events": events, "served_by": worst_tier(state.get("served_by"), tier)}
        except AllModelsDown:
            # No model available: say so plainly instead of inventing a review.
            critique = EvaluationCritique(
                clarity_score=0.0,
                advisory_notes=["Automated review unavailable (model unreachable); results above come from the real test run."],
            )
            events.append({"stage": "evaluator_advisory", "source": "unavailable", "critique": critique.model_dump()})
            return {"events": events}
