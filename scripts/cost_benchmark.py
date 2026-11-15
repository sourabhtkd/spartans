"""R4 cost benchmark: baseline vs optimized on the fixed 20-request script.

  uv run python scripts/cost_benchmark.py            # needs GROQ_API_KEY
  uv run python scripts/cost_benchmark.py --offline  # simulated provider, clearly labelled
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

from codeforge.bench.cost import load_script, render_report, run_mode, verdict
from codeforge.config import get_settings


def golden_summary() -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "evals", "-q", "-m", "not live", "-p", "no:cacheprovider"],
        capture_output=True, text=True, check=False,
    )
    m = re.search(r"(\d+ passed(?:, \d+ failed)?)", proc.stdout)
    return m.group(1) if m else "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--skip-golden", action="store_true")
    args = ap.parse_args()
    if not args.offline and not get_settings().groq_api_key:
        print("Set GROQ_API_KEY for a real run, or pass --offline for a labelled simulated run.")
        return 2

    script = load_script()
    mode = "SIMULATED provider (offline)" if args.offline else "LIVE provider"
    print(f"Cost benchmark [{mode}]: baseline...")
    base = run_mode("baseline", script, args.offline)
    print("optimized...")
    opt = run_mode("optimized", script, args.offline)
    meta = {"mode": mode, "golden": None if args.skip_golden else golden_summary()}
    out = Path("reports") / "cost_table.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(render_report(base, opt, meta), encoding="utf-8")
    v = verdict(base, opt)
    print(f"saving {v['saving_pct']:.1f}% | quality drop: {not v['no_quality_drop']} | report: {out}")
    return 0 if v["meets_target"] else 1


if __name__ == "__main__":
    sys.exit(main())
