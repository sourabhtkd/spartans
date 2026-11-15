import os
import re
import resource
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable

from codeforge.guard.pii import mask_text
from codeforge.state import RunResult

_SITECUSTOMIZE_CONTENT = """
import socket

_orig_socket = socket.socket

class BlockedSocket(_orig_socket):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("Network access is blocked in the CodeForge sandbox.")

socket.socket = BlockedSocket
"""


def _get_preexec_fn(timeout_s: int, mem_mb: int = 512) -> Callable[[], None]:
    def set_rlimits() -> None:
        # Limit CPU time
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (timeout_s + 1, timeout_s + 2))
        except (ValueError, OSError):
            pass

        # Limit file size to 1MB
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        except (ValueError, OSError):
            pass

        # RLIMIT_AS on Linux only; macOS raises ValueError for RLIMIT_AS with Python processes
        if sys.platform.startswith("linux"):
            try:
                mem_bytes = mem_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            except (ValueError, OSError):
                pass

    return set_rlimits


def parse_pytest_counts(output: str) -> tuple[int, int, str | None]:
    """Parses passed count, failed count, and first failing test name from pytest output."""
    passed = 0
    failed = 0
    last_failing_test: str | None = None

    passed_match = re.search(r"(\d+)\s+passed", output)
    if passed_match:
        passed = int(passed_match.group(1))

    failed_match = re.search(r"(\d+)\s+failed", output)
    if failed_match:
        failed = int(failed_match.group(1))

    failing_test_match = re.search(r"FAILED\s+([^\n]+)", output)
    if failing_test_match:
        last_failing_test = failing_test_match.group(1).strip()

    return passed, failed, last_failing_test


def run_pytest_sandboxed(
    code: str,
    tests: str,
    timeout_s: int = 10,
    mem_mb: int = 512,
) -> RunResult:
    """Executes code and pytest tests in an isolated temporary directory sandbox
    with scrubbed environment, no network access, rlimits, and process group timeouts.
    """
    with tempfile.TemporaryDirectory(prefix="cf_sandbox_") as tmpdir:
        solution_path = os.path.join(tmpdir, "solution.py")
        test_path = os.path.join(tmpdir, "test_solution.py")
        sitecustomize_path = os.path.join(tmpdir, "sitecustomize.py")

        with open(solution_path, "w", encoding="utf-8") as f:
            f.write(code)
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(tests)
        with open(sitecustomize_path, "w", encoding="utf-8") as f:
            f.write(_SITECUSTOMIZE_CONTENT)

        clean_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": tmpdir,
            "HOME": tmpdir,
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:langsmith",
            "--tb=short",
            test_path,
        ]

        proc = subprocess.Popen(
            cmd,
            cwd=tmpdir,
            env=clean_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_get_preexec_fn(timeout_s=timeout_s, mem_mb=mem_mb),  # noqa: PLW1509 - required for sandbox rlimits
        )

        timed_out = False
        exit_code: int | None = None
        try:
            stdout_b, stderr_b = proc.communicate(timeout=timeout_s)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            stdout_b, stderr_b = proc.communicate()

        raw_output = (
            stdout_b.decode("utf-8", errors="replace")
            + "\n"
            + stderr_b.decode("utf-8", errors="replace")
        )
        if timed_out:
            raw_output = f"Execution timed out after {timeout_s}s.\n" + raw_output

        passed, failed, last_failing_test = parse_pytest_counts(raw_output)
        masked_output = mask_text(raw_output[:4096])

        return RunResult(
            exit_code=exit_code,
            timed_out=timed_out,
            passed=passed,
            failed=failed,
            stdout=masked_output,
            last_failing_test=last_failing_test,
            cached=False,
        )
