"""Chat conversations and their messages, scoped to the active identity (like RunStore)."""
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from codeforge.config import Identity, current_identity
from codeforge.store.db import Conversation, Message, init_db

ASSISTANT_HISTORY_CHARS = 1200  # how much of a past reply (incl. code) is fed back as context


class ConversationNotFound(Exception):
    """A conversation is missing or belongs to someone else (the two are indistinguishable)."""


def new_conversation_id() -> str:
    return f"conv_{uuid.uuid4().hex[:8]}"


def _title_from(text: str) -> str:
    one_line = " ".join(text.split())
    return (one_line[:60] + "…") if len(one_line) > 60 else one_line or "New chat"


class ConversationStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        identity_provider: Callable[[], Identity] = current_identity,
    ) -> None:
        self.session_factory = session_factory or init_db()
        self.identity_provider = identity_provider

    def _owner(self) -> str:
        return self.identity_provider().user_id

    def _owned(self, session: Session, conversation_id: str) -> Conversation:
        conv = session.get(Conversation, conversation_id)
        if conv is None or conv.owner_id != self._owner():
            raise ConversationNotFound(conversation_id)
        return conv

    def create(self) -> str:
        conv_id = new_conversation_id()
        with self.session_factory() as session:
            session.add(Conversation(id=conv_id, owner_id=self._owner()))
            session.commit()
        return conv_id

    def list_mine(self) -> list[Conversation]:
        with self.session_factory() as session:
            return (
                session.query(Conversation)
                .filter(Conversation.owner_id == self._owner())
                .order_by(Conversation.updated_at.desc())
                .all()
            )

    def get(self, conversation_id: str) -> Conversation:
        with self.session_factory() as session:
            return self._owned(session, conversation_id)

    def messages(self, conversation_id: str) -> list[Message]:
        with self.session_factory() as session:
            self._owned(session, conversation_id)
            return (
                session.query(Message)
                .filter(Message.conversation_id == conversation_id)
                .order_by(Message.id)
                .all()
            )

    def append(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        code: str = "",
        tests: str = "",
        thread_id: str = "",
        status: str = "done",
        served_by: str = "",
        trace_id: str = "",
    ) -> Message:
        with self.session_factory() as session:
            conv = self._owned(session, conversation_id)
            msg = Message(
                conversation_id=conversation_id, role=role, content=content, code=code, tests=tests,
                thread_id=thread_id, status=status, served_by=served_by, trace_id=trace_id,
            )
            session.add(msg)
            if role == "user" and conv.title == "New chat":
                conv.title = _title_from(content)
            conv.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(msg)
            return msg

    def update_assistant_message(self, thread_id: str, **fields: Any) -> bool:
        """Update the assistant message of a pipeline run (used when a paused run finishes)."""
        with self.session_factory() as session:
            msg = (
                session.query(Message)
                .filter(Message.thread_id == thread_id, Message.role == "assistant")
                .first()
            )
            if msg is None:
                return False
            self._owned(session, msg.conversation_id)
            for key, value in fields.items():
                setattr(msg, key, value)
            session.commit()
            return True

    def history(self, conversation_id: str) -> list[dict[str, str]]:
        """Prior turns as ``[{role, content}]`` for ``submit(history=...)``.

        Assistant turns include the generated code so follow-ups ("add type hints") can build on it.
        """
        turns: list[dict[str, str]] = []
        for m in self.messages(conversation_id):
            if m.role == "assistant":
                text = f"Generated code:\n{m.code}" if m.code else m.content
                turns.append({"role": "assistant", "content": text[:ASSISTANT_HISTORY_CHARS]})
            else:
                turns.append({"role": "user", "content": m.content})
        return turns
