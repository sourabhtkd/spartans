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
