"""SQLAlchemy 2.0 models and engine/session plumbing.

Schema is created with ``create_all`` (no migrations). If you change a table,
delete the local sqlite file; this is documented in the README.
"""
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    idem_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    request_masked: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    tests: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="done")  # running | done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Approval(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    requester_id: Mapped[str] = mapped_column(String(64))
    approver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    reasons: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(String(32), default="pending")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Ticket(Base):
    __tablename__ = "tickets"

    ticket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    priority: Mapped[str] = mapped_column(String(16))
    sla_hours: Mapped[int] = mapped_column(Integer, default=24)
    request_masked: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_failing_test: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    agent: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_s: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    thumb: Mapped[str] = mapped_column(String(8))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    embedding_json: Mapped[str] = mapped_column(Text)
    guard_tokens: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    tests: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(8), default="")
    role: Mapped[str] = mapped_column(String(32), default="student")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PolicyDenial(Base):
    """One row per policy denial; used for the 'repeated denials' escalation rule."""

    __tablename__ = "policy_denials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


def _default_db_url() -> str:
    import os

    from codeforge.config import get_settings

    env_url = os.environ.get("CODEFORGE_DB_URL")
    if env_url:
        return env_url
    return str(get_settings().storage.get("db_url", "sqlite:///codeforge.db"))


def _enable_wal(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


_ENGINE_LOCK = threading.Lock()
_ENGINES: dict[str, Engine] = {}
_SESSIONMAKERS: dict[str, "sessionmaker[Any]"] = {}


def _file_engine(db_url: str) -> Engine:
    # Lock: concurrent first calls must not race on create_all.
    with _ENGINE_LOCK:
        engine = _ENGINES.get(db_url)
        if engine is None:
            engine = create_engine(db_url, connect_args={"check_same_thread": False, "timeout": 10})
            event.listen(engine, "connect", _enable_wal)
            Base.metadata.create_all(bind=engine)
            _ENGINES[db_url] = engine
        return engine


def init_db(db_url: str | None = None) -> "sessionmaker[Any]":
    """Return a session factory. File DBs share one cached engine per URL;
    in-memory URLs always get a fresh, isolated engine (used by tests)."""
    url = db_url or _default_db_url()
    if ":memory:" in url:
        engine = create_engine(url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with _ENGINE_LOCK:
        factory = _SESSIONMAKERS.get(url)
    if factory is None:
        factory = sessionmaker(
            bind=_file_engine(url), autoflush=False, autocommit=False, expire_on_commit=False
        )
        with _ENGINE_LOCK:
            factory = _SESSIONMAKERS.setdefault(url, factory)
    return factory
