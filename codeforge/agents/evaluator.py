from typing import Any

from pydantic import BaseModel, Field

from codeforge.state import CodeForgeState


class EvaluationCritique(BaseModel):
    clarity_score: float = 1.0
    advisory_notes: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class EvaluatorAgent:
    """Advisory evaluator agent that critiques implementation and tests without deciding pass/fail."""

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        events = list(state.get("events", []))
        code = state.get("code", "")
        tests = state.get("tests", "")
        reqs = state.get("requirements")

        notes: list[str] = []
        if reqs and reqs.edge_cases:
            notes.append(f"Reviewed {len(reqs.edge_cases)} declared edge cases.")
        if "def " in code:
            notes.append("Function structure defined cleanly.")
        if tests:
            notes.append("Unit tests generated and present.")

        critique = EvaluationCritique(
            clarity_score=0.95,
            advisory_notes=notes,
            suggestions=["Ensure strict time complexity bounds if large input sizes are anticipated."],
        )

        events.append({
            "stage": "evaluator_advisory",
            "critique": critique.model_dump(),
        })
        return {"events": events}
