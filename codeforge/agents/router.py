"""Router: decides what kind of request this is (Python function, SQL, or something else)."""
import re
from typing import Any, Literal

from codeforge.agents.base import BaseAgent, request_with_context
from codeforge.llm import AllModelsDown, resilient_invoke
from codeforge.state import CodeForgeState, RouteDecision

RouteKind = Literal["python_function", "sql", "other"]
LANGUAGE_FOR_KIND = {"python_function": "python", "sql": "sql", "other": "none"}

SQL_HINTS = re.compile(r"\bsql\b|\bselect\b.+\bfrom\b|\bjoin\b|\b(?:query|queries)\b.*\btable\b")
NOT_CODE_HINTS = re.compile(r"\b(?:poem|story|essay|joke|recipe|weather|translate|capital of|who is|what is the)\b")


def route_by_keywords(request: str) -> RouteKind:
    """Plain-Python routing used when no model is available.

    Defaults to python: with models down we cannot know better, and the pipeline then answers from
    templates or refuses honestly (with a ticket) rather than guessing.
    """
    text = request.lower()
    if SQL_HINTS.search(text):
        return "sql"
    if NOT_CODE_HINTS.search(text):
        return "other"
    return "python_function"


class RouterAgent(BaseAgent):
    name = "router"

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        messages = self.messages(request_with_context(state))
        try:
            decision, tier = resilient_invoke(self.name, messages, RouteDecision)
            return {"route": decision, "served_by": self.served_by(state, tier)}
        except AllModelsDown:
            kind = route_by_keywords(state.get("request_masked", ""))
            route = RouteDecision(kind=kind, language=LANGUAGE_FOR_KIND[kind], confidence=0.7)
            return {"route": route, "served_by": self.served_by(state, "template")}
