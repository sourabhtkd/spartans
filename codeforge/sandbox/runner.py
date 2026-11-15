import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable

from codeforge.privacy.pii import mask_text
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
_MAX_RAW_CHARS = 65536
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
    # Bound regex work on huge outputs; anything past the cap is dropped by the final truncation anyway.
    return mask_text(raw[:_MAX_RAW_CHARS])[:limit]


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
    """Read (passed, failed, first failing test name) from pytest's summary output."""

    def count(pattern: str) -> int:
        match = re.search(pattern, output)
        return int(match.group(1)) if match else 0

    failing = re.search(r"FAILED\s+([^\n]+)", output)
    return count(r"(\d+)\s+passed"), count(r"(\d+)\s+failed"), failing.group(1).strip() if failing else None


def _write_sandbox_files(tmpdir: str, code: str, tests: str) -> None:
    for filename, content in (
        ("solution.py", code),
        ("test_solution.py", tests),
        ("sitecustomize.py", _SITECUSTOMIZE_CONTENT),  # blocks sockets inside the child
    ):
        with open(os.path.join(tmpdir, filename), "w", encoding="utf-8") as f:
            f.write(content)


def _scrubbed_env(tmpdir: str) -> dict[str, str]:
    """No host PATH, no secrets, no user site, no pytest plugins, dead proxies."""
    return {
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


def _docker_command(tmpdir: str, mem_mb: int) -> list[str]:
    image = os.environ.get("SANDBOX_DOCKER_IMAGE", "codeforge-sandbox")
    return [
        "docker", "run", "--rm", "--network", "none", "--memory", f"{mem_mb}m",
        "--pids-limit", "64", "--read-only", "--tmpfs", "/tmp",
        "-v", f"{tmpdir}:/w:ro", "-w", "/w", "-e", "PYTHONPATH=/w",
        "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", image,
        "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "/w/test_solution.py",
    ]


def _build_command(tmpdir: str, mem_mb: int) -> tuple[list[str], dict[str, str], str]:
    """Pick the isolation backend. Returns (command, environment, isolation label)."""
    if os.environ.get("SANDBOX_BACKEND", "local") == "docker":
        return _docker_command(tmpdir, mem_mb), {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, "docker_network_none"

    pytest_cmd = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:langsmith",
        "--tb=short", os.path.join(tmpdir, "test_solution.py"),
    ]
    if _unshare_available():
        return ["unshare", "-rn", *pytest_cmd], _scrubbed_env(tmpdir), "unshare_netns"
    return pytest_cmd, _scrubbed_env(tmpdir), "python_socket_block"


def _run_with_timeout(
    cmd: list[str], env: dict[str, str], cwd: str, timeout_s: int, mem_mb: int
) -> tuple[int | None, bool, str]:
    """Run the command; on timeout kill its whole process group. Returns (exit_code, timed_out, output)."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # own process group, so the kill below also reaches children
        preexec_fn=_get_preexec_fn(timeout_s=timeout_s, mem_mb=mem_mb),  # noqa: PLW1509 - required for rlimits
    )
    exit_code: int | None = None
    timed_out = False
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

    output = stdout_b.decode("utf-8", errors="replace") + "\n" + stderr_b.decode("utf-8", errors="replace")
    if timed_out:
        output = f"Execution timed out after {timeout_s}s.\n" + output
    return exit_code, timed_out, output


def run_pytest_sandboxed(code: str, tests: str, timeout_s: int = 10, mem_mb: int = 512) -> RunResult:
    """Run `tests` against `code` in a throwaway directory with pytest, in a restricted subprocess.

    Restrictions: scrubbed environment, no network, resource limits, hard timeout.
    The caller decides pass/fail from the returned exit code, never from the output text.
    """
    with tempfile.TemporaryDirectory(prefix="cf_sandbox_") as tmpdir:
        _write_sandbox_files(tmpdir, code, tests)
        cmd, env, isolation = _build_command(tmpdir, mem_mb)
        exit_code, timed_out, output = _run_with_timeout(cmd, env, tmpdir, timeout_s, mem_mb)

    passed, failed, last_failing_test = parse_pytest_counts(output)
    return RunResult(
        exit_code=exit_code,
        timed_out=timed_out,
        passed=passed,
        failed=failed,
        stdout=mask_and_truncate(output),
        last_failing_test=last_failing_test,
        isolation=isolation,
    )
