"""R5 chaos run: 20 requests under 60% injected provider failures, then a total outage.

Usage:
  uv run python scripts/chaos_run.py            # needs GROQ_API_KEY (live provider)
  uv run python scripts/chaos_run.py --offline  # simulated provider, clearly labelled
"""
import argparse
import sys
from pathlib import Path

from codeforge.bench.chaos import (
    TEMPLATE_TASKS,
    UNKNOWN_TASKS,
    render_report,
    run_requests,
)
from codeforge.bench.requests import CHAOS_REQUESTS as REQUESTS
from codeforge.config import get_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use the simulated provider (no API key)")
    ap.add_argument("--rate", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not args.offline and not get_settings().groq_api_key:
        print("Set GROQ_API_KEY for a real chaos run, or pass --offline for a labelled simulated run.")
        return 2

    mode = "SIMULATED provider (offline)" if args.offline else "LIVE provider"
    print(f"Chaos run [{mode}]: {len(REQUESTS)} requests at {args.rate:.0%} failure rate...")
    chaos = run_requests(REQUESTS, args.rate, args.seed, args.offline, fast=args.offline)
    outage = run_requests(TEMPLATE_TASKS + UNKNOWN_TASKS, 0.0, args.seed, args.offline, outage=True)

    out = Path("reports") / "chaos_log.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(render_report(chaos, outage, {"mode": mode, "rate": args.rate, "seed": args.seed}), encoding="utf-8")
    answered = sum(r.answered for r in chaos)
    print(f"{answered}/{len(chaos)} answered under chaos | log: {out}")
    return 0 if answered == len(chaos) else 1


if __name__ == "__main__":
    sys.exit(main())
