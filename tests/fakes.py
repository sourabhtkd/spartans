from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class FakeLLM:
    """Mock LLM callable to replace resilient_invoke in tests."""

    def __init__(self, canned_responses: dict[str, BaseModel] | None = None) -> None:
        self.canned_responses: dict[str, BaseModel] = canned_responses or {}
        self.invocations: list[dict[str, Any]] = []

    def set_response(self, agent: str, response: BaseModel) -> None:
        self.canned_responses[agent] = response

    def __call__(
        self, agent: str, messages: Any, schema: type[T]
    ) -> tuple[T, str]:
        self.invocations.append({
            "agent": agent,
            "messages": messages,
            "schema": schema,
        })
        if agent in self.canned_responses:
            resp = self.canned_responses[agent]
            return resp, "primary"  # type: ignore[return-value]
        # Instantiate empty/default schema instance if possible
        return schema.model_construct(), "primary"
