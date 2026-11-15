import threading
import time

import codeforge.graph as g
from codeforge.state import RunResult
from codeforge.store.runs import RunStore


def test_concurrent_identical_runs_execute_once(monkeypatch):
    calls = []

    def slow_sandbox(code, tests, timeout_s=10, **_):
        calls.append(1)
        time.sleep(0.5)
        return RunResult(exit_code=0, passed=1)

    monkeypatch.setattr(g, "run_pytest_sandboxed", slow_sandbox)
    state = {"code": "def f(): return 1", "tests": "t", "thread_id": "th"}
    results = []

    def worker():
        results.append(g.sandbox_run_node(dict(state)))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert len(results) == 4
    assert all(r["run_result"].exit_code == 0 for r in results)


def test_first_result_wins_and_claim_is_exclusive():
    store = RunStore()
    assert store.claim("r1", "k", "req", "c", "t") is True
    assert store.claim("r2", "k", "req", "c", "t") is False
    store.complete("k", RunResult(exit_code=0, passed=1))
    store.complete("k", RunResult(exit_code=1, failed=1))  # must not overwrite
    assert store.get_by_idem_key("k").exit_code == 0
