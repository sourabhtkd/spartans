"""Golden-set gate (R6).

HARD cases (identity, PII, policy, approval, idempotency, fail-closed...) must pass 100%.
SOFT cases (routing, requirements, correctness, escalation) must pass >= 85% of those that ran.
LLM-backed cases (mode: "llm") replay recorded cassettes; without a cassette they SKIP (and are
reported as skipped, never counted as passed). Record them with `pytest evals --record` + a real key.
Correctness cases run generated code against HIDDEN reference tests in evals/reference_tests/.

Each case in golden_set.json has a `kind`; evals/checks/ maps kinds to the function that verifies them.
"""
import json
from pathlib import Path
from typing import Any

import pytest

from evals.checks import CHECKS
from evals.checks.common import Env

CASES = json.loads((Path(__file__).resolve().parent / "golden_set.json").read_text(encoding="utf-8"))
_RESULTS: dict[str, str] = {}  # case id -> passed | failed | skipped


def test_every_case_kind_has_a_check():
    missing = {c["kind"] for c in CASES} - set(CHECKS)
    assert not missing, f"golden cases with no check (would pass vacuously): {missing}"


def test_golden_set_shape():
    assert len(CASES) >= 45
    assert sum(c["tier"] == "hard" for c in CASES) >= 15
    assert sum(c["tier"] == "soft" for c in CASES) >= 12
    assert len({c["id"] for c in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, as_profile: Any, request: Any) -> None:
    env = Env(monkeypatch, tmp_path, as_profile, request.config.getoption("--record"))
    try:
        CHECKS[case["kind"]](case, env)
        _RESULTS[case["id"]] = "passed"
    except pytest.skip.Exception:
        _RESULTS[case["id"]] = "skipped"
        raise
    except BaseException:
        _RESULTS[case["id"]] = "failed"
        raise


def test_zz_golden_gate_summary() -> None:
    """Gate: hard 100%, soft >= 85% of the soft cases that ran. Names every failing case."""
    if any(c["id"] not in _RESULTS for c in CASES):
        pytest.skip("partial run (-k / -x): gate summary needs the full golden set")
    hard = [c["id"] for c in CASES if c["tier"] == "hard"]
    soft = [c["id"] for c in CASES if c["tier"] == "soft"]
    hard_bad = [i for i in hard if _RESULTS[i] != "passed"]
    soft_ran = [i for i in soft if _RESULTS[i] != "skipped"]
    soft_bad = [i for i in soft_ran if _RESULTS[i] == "failed"]
    soft_skipped = [i for i in soft if _RESULTS[i] == "skipped"]
    hard_pct = 100 * (len(hard) - len(hard_bad)) / len(hard)
    soft_pct = 100 * (len(soft_ran) - len(soft_bad)) / len(soft_ran) if soft_ran else 0.0
    print(
        f"\nGOLDEN GATE: HARD {hard_pct:.1f}% ({len(hard) - len(hard_bad)}/{len(hard)}) | "
        f"SOFT {soft_pct:.1f}% ({len(soft_ran) - len(soft_bad)}/{len(soft_ran)} ran, {len(soft_skipped)} skipped: no cassette)"
    )
    assert not hard_bad, f"HARD gate failed (must be 100%): {hard_bad}"
    assert soft_pct >= 85.0, f"SOFT gate failed (<85%): {soft_bad}"
