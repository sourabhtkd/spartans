"""Single entrypoint for user text.

User text is masked *here*, before it enters the graph, so the LangGraph
checkpointer never persists raw secrets/PII. Identity is never taken from the
caller: ``ingress_guard`` always reads it from config.
"""
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from codeforge.config import current_identity
from codeforge.guard.pii import mask_obj, mask_text
from codeforge.observability import current_request_id


@dataclass
class SubmitResult:
    thread_id: str
    values: dict[str, Any] = field(default_factory=dict)
    next_nodes: tuple[str, ...] = ()

    @property
    def pending_approval(self) -> bool:
        return "human_approval" in self.next_nodes


def new_thread_id() -> str:
    return f"run_{uuid.uuid4().hex[:8]}"


def submit(
    raw_text: str,
    thread_id: str | None = None,
    graph: Any | None = None,
    history: list[dict[str, Any]] | None = None,
) -> SubmitResult:
    """Mask the request, run the graph, and return the resulting state snapshot."""
    if graph is None:
        from codeforge.pipeline import build_graph

        graph = build_graph()
    tid = thread_id or new_thread_id()
    # Bind ids for metering (request_id) and structured logs (thread_id, user_id).
    current_request_id.set(tid)
    structlog.contextvars.bind_contextvars(thread_id=tid, user_id=current_identity().user_id)
    config = {"configurable": {"thread_id": tid}}
    initial: dict[str, Any] = {
        "thread_id": tid,
        "raw_request_masked": mask_text(raw_text),
    }
    if history:
        initial["history"] = mask_obj(history)
    graph.invoke(initial, config=config)
    snap = graph.get_state(config)
    return SubmitResult(thread_id=tid, values=dict(snap.values), next_nodes=tuple(snap.next))
