import os
import re
import resource
import shutil
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


_MAX_OUTPUT_CHARS = 4096
_UNSHARE_OK: bool | None = None


def _unshare_available() -> bool:
    """True when `unshare -rn` (new user+network namespace) works: real network isolation on Linux."""
    global _UNSHARE_OK
    if _UNSHARE_OK is None:
        _UNSHARE_OK = False
        if sys.platform.startswith("linux") and shutil.which("unshare"):
            try:
                _UNSHARE_OK = (
                    subprocess.run(["unshare", "-rn", "true"], check=False, capture_output=True, timeout=5).returncode == 0
                )
            except (OSError, subprocess.SubprocessError):
                _UNSHARE_OK = False
    return _UNSHARE_OK


def mask_and_truncate(raw: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    """Mask BEFORE truncating so a secret cut at the boundary can't slip past the regexes."""
    return mask_text(raw)[:limit]


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

        # Open files, and (Linux only) process count to blunt fork bombs.
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        except (ValueError, OSError):
            pass
        if sys.platform.startswith("linux"):
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
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

        # Scrubbed environment: no host PATH, no secrets, no user site, no pytest plugins, dead proxies.
        clean_env = {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": tmpdir,
            "HOME": tmpdir,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "NO_PROXY": "*",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
        }

        backend = os.environ.get("SANDBOX_BACKEND", "local")
        isolation = "python_socket_block"
        prefix: list[str] = []
        if backend == "docker":
            isolation = "docker_network_none"
        elif _unshare_available():
            prefix = ["unshare", "-rn"]
            isolation = "unshare_netns"

        cmd = [
            *prefix,
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

        if backend == "docker":
            image = os.environ.get("SANDBOX_DOCKER_IMAGE", "codeforge-sandbox")
            cmd = [
                "docker", "run", "--rm", "--network", "none", "--memory", f"{mem_mb}m",
                "--pids-limit", "64", "--read-only", "--tmpfs", "/tmp",
                "-v", f"{tmpdir}:/w:ro", "-w", "/w", "-e", "PYTHONPATH=/w",
                "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", image,
                "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "/w/test_solution.py",
            ]
            clean_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

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
        masked_output = mask_and_truncate(raw_output)

        return RunResult(
            exit_code=exit_code,
            timed_out=timed_out,
            passed=passed,
            failed=failed,
            stdout=masked_output,
            last_failing_test=last_failing_test,
            cached=False,
            isolation=isolation,
        )
