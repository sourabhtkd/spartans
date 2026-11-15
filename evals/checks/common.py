"""Shared pieces for the golden checks: the Env passed to every check, and a few reusable builders."""
from pathlib import Path
from typing import Any

import pytest

from codeforge.sandbox.runner import run_pytest_sandboxed
from codeforge.state import RunResult
from tests.helpers import REFERENCE_DIR, memory_graph, patch_llm, scripted_fake

ROOT = Path(__file__).resolve().parents[2]  # repository root

RISKY = "import tempfile\n\ndef w():\n    with open('x.txt', 'w') as f:\n        f.write('hi')\n    return 1\n"
RISKY_TESTS = "from solution import w\n\ndef test_w():\n    assert w() == 1\n    assert w() == 1\n    assert w() == 1\n"
FIXED = "def f(x):\n    return x + 1\n"


class Env:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, as_profile: Any, record: bool) -> None:
        self.mp, self.tmp, self.as_profile, self.record = monkeypatch, tmp_path, as_profile, record


def run_reference(code: str, reference: str) -> RunResult:
    ref = (REFERENCE_DIR / reference).read_text(encoding="utf-8")
    return run_pytest_sandboxed(code, ref, timeout_s=10)


def risky_graph(env: Env) -> Any:
    patch_llm(env.mp, scripted_fake(RISKY, RISKY_TESTS, "w"))
    return memory_graph()


# ---------------------------------------------------------------- HARD handlers
