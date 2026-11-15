import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from codeforge.config import Identity, current_identity, get_settings
from codeforge.policy.code_policy import POLICY_VERSION
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
        """Looks up a *finished* result by idempotency key (running claims return None)."""
        with self.session_factory() as session:
            run = session.query(Run).filter(Run.idem_key == idem_key).first()
            if run and run.status == "done":
                data = json.loads(run.result_json)
                data["cached"] = True
                return RunResult(**data)
            return None

    def claim(
        self,
        run_id: str,
        idem_key: str,
        request_masked: str,
        code: str,
        tests: str,
        stale_after_s: float = 60.0,
    ) -> bool:
        """Atomically claim the right to execute. True = caller must run; False = someone else did/does.

        Relies on the UNIQUE constraint on idem_key, so concurrent submissions cannot both claim.
        A claim stuck in 'running' longer than ``stale_after_s`` (crashed worker) can be taken over.
        """
        identity = self._get_identity()
        with self.session_factory() as session:
            try:
                session.add(
                    Run(
                        run_id=run_id,
                        owner=identity.user_id,
                        idem_key=idem_key,
                        request_masked=request_masked,
                        code=code,
                        tests=tests,
                        result_json="{}",
                        status="running",
                        policy_version=POLICY_VERSION,
                        created_at=datetime.now(UTC),
                    )
                )
                session.commit()
                return True
            except IntegrityError:
                session.rollback()
            cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=stale_after_s)
            taken = (
                session.query(Run)
                .filter(Run.idem_key == idem_key, Run.status == "running", Run.created_at < cutoff)
                .update({"created_at": datetime.now(UTC)})
            )
            session.commit()
            return taken == 1

    def complete(self, idem_key: str, result: RunResult) -> None:
        """Marks a claimed run as done. Never overwrites an already-done result."""
        with self.session_factory() as session:
            session.query(Run).filter(Run.idem_key == idem_key, Run.status == "running").update(
                {"status": "done", "result_json": result.model_dump_json()}
            )
            session.commit()

    def wait_for_result(self, idem_key: str, max_wait_s: float, poll_s: float = 0.1) -> RunResult | None:
        """Poll for another worker's result for the same idempotency key."""
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline:
            res = self.get_by_idem_key(idem_key)
            if res is not None:
                return res
            time.sleep(poll_s)
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
        """Persists a completed run for the current identity. The first stored result wins."""
        identity = self._get_identity()
        with self.session_factory() as session:
            existing = session.query(Run).filter(Run.idem_key == idem_key).first()
            if existing:
                if existing.status != "done":
                    existing.status = "done"
                    existing.result_json = result.model_dump_json()
                    session.commit()
                return existing

            new_run = Run(
                run_id=run_id,
                owner=identity.user_id,
                idem_key=idem_key,
                request_masked=request_masked,
                code=code,
                tests=tests,
                result_json=result.model_dump_json(),
                status="done",
                policy_version=POLICY_VERSION,
                created_at=datetime.now(UTC),
            )
            session.add(new_run)
            session.commit()
            return new_run
