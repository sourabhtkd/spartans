import time

from codeforge.sandbox.runner import run_pytest_sandboxed


def test_sandbox_passing_code():
    code = (
        "def is_palindrome(s: str) -> bool:\n"
        "    return s == s[::-1]\n"
    )
    tests = (
        "from solution import is_palindrome\n\n"
        "def test_cases():\n"
        "    assert is_palindrome('radar') is True\n"
        "    assert is_palindrome('hello') is False\n"
        "    assert is_palindrome('') is True\n"
    )
    res = run_pytest_sandboxed(code, tests, timeout_s=5)
    assert res.exit_code == 0
    assert not res.timed_out
    assert res.passed >= 1
    assert res.failed == 0


def test_sandbox_timeout_infinite_loop():
    code = (
        "def infinite_fn():\n"
        "    while True:\n"
        "        pass\n"
    )
    tests = (
        "from solution import infinite_fn\n\n"
        "def test_infinite():\n"
        "    infinite_fn()\n"
    )
    start = time.time()
    res = run_pytest_sandboxed(code, tests, timeout_s=2)
    elapsed = time.time() - start

    assert res.timed_out is True
    assert res.exit_code is None
    assert elapsed < 5.0


def test_sandbox_socket_network_blocked():
    code = (
        "import socket\n"
        "def try_network():\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    return s\n"
    )
    tests = (
        "import pytest\n"
        "from solution import try_network\n\n"
        "def test_socket():\n"
        "    with pytest.raises(Exception) as excinfo:\n"
        "        try_network()\n"
        "    assert 'Network access is blocked' in str(excinfo.value)\n"
    )
    res = run_pytest_sandboxed(code, tests, timeout_s=5)
    assert res.exit_code == 0
    assert res.passed >= 1


def test_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "super-secret-value")
    code = "import os\n\ndef leak():\n    return os.environ.get('SECRET_TOKEN'), os.environ.get('PATH')\n"
    tests = (
        "from solution import leak\n\n"
        "def test_leak():\n"
        "    tok, path = leak()\n"
        "    assert tok is None\n"
        "    assert path == '/usr/bin:/bin'\n"
    )
    res = run_pytest_sandboxed(code, tests, timeout_s=10)
    assert res.exit_code == 0, res.stdout


def test_mask_before_truncate():
    from codeforge.sandbox.runner import mask_and_truncate

    raw = "x" * 4080 + " sk-abcdefghijklmnop1234 tail"
    out = mask_and_truncate(raw, limit=4096)
    assert "sk-" not in out
    assert "[SECRET]" in out


def test_isolation_mode_is_reported():
    code = "def f():\n    return 1\n"
    tests = "from solution import f\n\ndef test_f():\n    assert f() == 1\n"
    res = run_pytest_sandboxed(code, tests, timeout_s=10)
    assert res.isolation in {"python_socket_block", "unshare_netns"}


def test_fork_bomb_is_contained():
    import sys

    import pytest

    if not sys.platform.startswith("linux"):
        pytest.skip("RLIMIT_NPROC enforced on Linux only")
    code = (
        "import os\n\n"
        "def bomb():\n"
        "    while True:\n"
        "        os.fork()\n"
    )
    tests = "from solution import bomb\n\ndef test_b():\n    bomb()\n"
    res = run_pytest_sandboxed(code, tests, timeout_s=3)
    assert res.timed_out or res.exit_code != 0
