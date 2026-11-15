from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class Run(Base):
    __tablename__ = "runs"

    run_id = Column(String(64), primary_key=True)
    owner = Column(String(64), nullable=False, index=True)
    idem_key = Column(String(64), unique=True, index=True, nullable=False)
    request_masked = Column(Text, nullable=False)
    code = Column(Text, nullable=False)
    tests = Column(Text, nullable=False)
    result_json = Column(Text, nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )


class Approval(Base):
    __tablename__ = "approvals"

    approval_id = Column(String(64), primary_key=True)
    thread_id = Column(String(64), nullable=False, index=True)
    requester_id = Column(String(64), nullable=False)
    approver_id = Column(String(64), nullable=True)
    code_hash = Column(String(64), nullable=False)
    reasons = Column(Text, nullable=False)
    verdict = Column(String(32), default="pending", nullable=False)
    comment = Column(Text, default="", nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at = Column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )


class Ticket(Base):
    __tablename__ = "tickets"

    ticket_id = Column(String(64), primary_key=True)
    priority = Column(String(16), nullable=False)
    sla_hours = Column(Integer, default=24, nullable=False)
    request_masked = Column(Text, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    last_failing_test = Column(Text, nullable=True)
    reason = Column(Text, nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent = Column(String(64), nullable=False)
    model = Column(String(64), nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    latency_s = Column(Float, default=0.0, nullable=False)
    cost_usd = Column(Float, default=0.0, nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, index=True)
    thumb = Column(String(8), nullable=False)
    comment = Column(Text, default="", nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    cache_key = Column(String(64), primary_key=True)
    request_text = Column(Text, nullable=False)
    embedding_json = Column(Text, nullable=False)
    guard_tokens = Column(Text, nullable=False)
    code = Column(Text, nullable=False)
    tests = Column(Text, nullable=False)
    policy_verdict = Column(String(32), default="allow", nullable=False)
    role = Column(String(32), default="student", nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )


def init_db(db_url: str = "sqlite:///codeforge.db"):
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
