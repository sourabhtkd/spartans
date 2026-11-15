import pytest

from codeforge.config import Identity
from codeforge.state import RunResult
from codeforge.store.db import init_db
from codeforge.store.runs import (
    QuotaExceeded,
    RunNotFound,
    RunStore,
    compute_idempotency_key,
)


@pytest.fixture
def test_db():
    # In-memory SQLite for tests
    return init_db("sqlite:///:memory:")


def test_cross_user_read_blocked_and_identical_error(test_db):
    alice = Identity(user_id="u_alice", role="student")
    bob = Identity(user_id="u_bob", role="student")

    store_alice = RunStore(session_factory=test_db, identity_provider=lambda: alice)
    store_bob = RunStore(session_factory=test_db, identity_provider=lambda: bob)

    # Alice saves a run
    res = RunResult(exit_code=0, passed=2, failed=0, stdout="OK")
    run_alice = store_alice.save_run(
        run_id="run_alice_1",
        idem_key="key_1",
        request_masked="palindrome",
        code="def f(): pass",
        tests="def test(): pass",
        result=res,
    )
    assert run_alice.run_id == "run_alice_1"

    # Alice can read her own run
    found = store_alice.get_run("run_alice_1")
    assert found.run_id == "run_alice_1"

    # Bob tries to read Alice's run -> RunNotFound("run not found")
    with pytest.raises(RunNotFound) as exc_foreign:
        store_bob.get_run("run_alice_1")
    assert str(exc_foreign.value) == "run not found"

    # Bob tries to read completely non-existent run -> exact same RunNotFound("run not found")
    with pytest.raises(RunNotFound) as exc_missing:
        store_bob.get_run("run_does_not_exist_999")
    assert str(exc_missing.value) == "run not found"
    assert str(exc_foreign.value) == str(exc_missing.value)


def test_quota_enforcement(test_db):
    alice = Identity(user_id="u_alice", role="student")
    store = RunStore(session_factory=test_db, identity_provider=lambda: alice)

    res = RunResult(exit_code=0, passed=1, failed=0, stdout="OK")
    # Student quota is 20 runs per hour
    for i in range(20):
        store.check_and_increment_quota()
        store.save_run(
            run_id=f"run_{i}",
            idem_key=f"key_{i}",
            request_masked="req",
            code="code",
            tests="tests",
            result=res,
        )

    # The 21st run should trip QuotaExceeded
    with pytest.raises(QuotaExceeded) as exc:
        store.check_and_increment_quota()
    assert "Quota exceeded" in str(exc.value)


def test_idempotency_caching(test_db):
    alice = Identity(user_id="u_alice", role="student")
    store = RunStore(session_factory=test_db, identity_provider=lambda: alice)

    code = "def is_palindrome(s): return s == s[::-1]"
    tests = "def test_p(): pass"
    key = compute_idempotency_key(alice.user_id, code, tests)

    # First lookup -> None
    assert store.get_by_idem_key(key) is None

    # Save run
    res = RunResult(exit_code=0, passed=3, failed=0, stdout="3 passed")
    store.save_run(
        run_id="run_idem_1",
        idem_key=key,
        request_masked="palindrome",
        code=code,
        tests=tests,
        result=res,
    )

    # Second lookup -> cached=True
    cached_res = store.get_by_idem_key(key)
    assert cached_res is not None
    assert cached_res.cached is True
    assert cached_res.passed == 3
