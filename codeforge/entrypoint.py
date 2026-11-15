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
from codeforge.observability import (
    current_request_id,
    current_session_id,
    langfuse_handler,
    trace_turn,
)
from codeforge.privacy.pii import mask_obj, mask_text


@dataclass
class SubmitResult:
    thread_id: str
    values: dict[str, Any] = field(default_factory=dict)
    next_nodes: tuple[str, ...] = ()
    trace_id: str = ""  # Langfuse trace of this turn ("" when tracing is off)

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
    session_id: str | None = None,
) -> SubmitResult:
    """Mask the request, run the graph, and return the resulting state snapshot."""
    if graph is None:
        from codeforge.pipeline import build_graph

        graph = build_graph()
    tid = thread_id or new_thread_id()
    # Bind ids for metering (request_id) and structured logs (thread_id, user_id).
    current_request_id.set(tid)
    current_session_id.set(session_id or "")
    identity = current_identity()
    structlog.contextvars.bind_contextvars(thread_id=tid, user_id=identity.user_id)
    if session_id:
        structlog.contextvars.bind_contextvars(session_id=session_id)
    masked = mask_text(raw_text)
    initial: dict[str, Any] = {
        "thread_id": tid,
        "request_masked": masked,
    }
    if history:
        initial["history"] = mask_obj(history)
    with trace_turn(
        session_id=session_id or tid,
        user_id=identity.user_id,
        name="chat_turn" if session_id else "run",
        tags=[identity.role, "codeforge"],
        input=masked,
    ) as turn:
        config: dict[str, Any] = {
            "configurable": {"thread_id": tid},
            "metadata": {
                "langfuse_session_id": session_id or tid,
                "langfuse_user_id": identity.user_id,
                "thread_id": tid,
            },
        }
        handler = langfuse_handler() if turn.trace_id else None
        if handler is not None:
            config["callbacks"] = [handler]
        graph.invoke(initial, config=config)
        snap = graph.get_state({"configurable": {"thread_id": tid}})
        report = snap.values.get("report")
        turn.output = report if isinstance(report, str) else None
    return SubmitResult(thread_id=tid, values=dict(snap.values), next_nodes=tuple(snap.next), trace_id=turn.trace_id)
