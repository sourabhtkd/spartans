import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from codeforge.config import Identity, current_identity, get_settings
from codeforge.state import RunResult
from codeforge.store.db import Run, init_db


class RunNotFound(Exception):
    """Raised when a run is missing or belongs to another user."""


class QuotaExceeded(Exception):
    """Raised when user hourly run quota is exceeded."""


def compute_idempotency_key(user_id: str, code: str, tests: str) -> str:
    """Computes SHA256 idempotency key = sha256(user_id + \x00 + code + \x00 + tests)."""
    raw = f"{user_id}\x00{code}\x00{tests}".encode()
    return hashlib.sha256(raw).hexdigest()


class RunStore:
    """Manages runs with strict identity scoping, quotas, and idempotency caching."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        identity_provider: Callable[[], Identity] = current_identity,
    ) -> None:
        self.session_factory = session_factory or init_db()
        self.identity_provider = identity_provider

    def _get_identity(self) -> Identity:
        return self.identity_provider()

    def get_my_runs(self) -> list[Run]:
        """Returns all runs belonging to the currently active identity.
        Takes NO user argument.
        """
        user_id = self._get_identity().user_id
        with self.session_factory() as session:
            return (
                session.query(Run)
                .filter(Run.owner == user_id)
                .order_by(Run.created_at.desc())
                .all()
            )

    def get_run(self, run_id: str) -> Run:
        """Retrieves a single run belonging to the currently active identity.
        Missing runs and runs owned by another user raise identical RunNotFound.
        Takes NO user argument.
        """
        user_id = self._get_identity().user_id
        with self.session_factory() as session:
            run = session.query(Run).filter(Run.run_id == run_id).first()
            if run is None or run.owner != user_id:
                raise RunNotFound("run not found")
            return run

    def check_and_increment_quota(self) -> None:
        """Verifies that the current user has not exceeded their hourly quota."""
        identity = self._get_identity()
        settings = get_settings()
        role_quotas = settings.quotas.get(identity.role, {})
        limit = role_quotas.get("runs_per_hour", 20)

        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        with self.session_factory() as session:
            recent_count = (
                session.query(Run)
                .filter(Run.owner == identity.user_id, Run.created_at >= one_hour_ago)
                .count()
            )
            if recent_count >= limit:
                raise QuotaExceeded(
                    f"Quota exceeded: {recent_count}/{limit} runs in the past hour for role '{identity.role}'"
                )

    def get_by_idem_key(self, idem_key: str) -> RunResult | None:
        """Looks up a cached result by idempotency key."""
        with self.session_factory() as session:
            run = session.query(Run).filter(Run.idem_key == idem_key).first()
            if run:
                data = json.loads(run.result_json)
                data["cached"] = True
                return RunResult(**data)
            return None

    def save_run(
        self,
        run_id: str,
        idem_key: str,
        request_masked: str,
        code: str,
        tests: str,
        result: RunResult,
    ) -> Run:
        """Persists a completed run record for the current identity."""
        identity = self._get_identity()
        with self.session_factory() as session:
            existing = session.query(Run).filter(Run.idem_key == idem_key).first()
            if existing:
                existing.result_json = result.model_dump_json()
                session.commit()
                session.refresh(existing)
                return existing

            new_run = Run(
                run_id=run_id,
                owner=identity.user_id,
                idem_key=idem_key,
                request_masked=request_masked,
                code=code,
                tests=tests,
                result_json=result.model_dump_json(),
                created_at=datetime.now(UTC),
            )
            session.add(new_run)
            session.commit()
            session.refresh(new_run)
            return new_run
