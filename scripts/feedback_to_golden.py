"""Flywheel: turn a recorded thumbs-down into a new golden case.

The case needs a HIDDEN reference test that captures what the user expected. Write it first
(evals/reference_tests/<name>.py), then promote the feedback:

  uv run python scripts/feedback_to_golden.py <feedback_id|run_id> --reference palindrome_case_insensitive.py

The new case is appended to evals/golden_set.json (it should FAIL until the bug is fixed).
"""
import argparse
import json
import sys
from pathlib import Path

from codeforge.store.db import Feedback, Run, init_db

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert feedback into a new golden-set case")
    parser.add_argument("feedback_id", help="feedback id or run_id to promote")
    parser.add_argument("--reference", required=True, help="hidden reference test file in evals/reference_tests/")
    parser.add_argument("--tier", default="soft", choices=["hard", "soft"])
    args = parser.parse_args()

    ref_path = ROOT / "evals" / "reference_tests" / args.reference
    if not ref_path.exists():
        print(f"Reference test not found: {ref_path}\nWrite it first; it encodes what the user expected.")
        return 2

    request_text, origin = args.feedback_id, "manual"
    with init_db()() as session:
        fb = None
        if args.feedback_id.isdigit():
            fb = session.query(Feedback).filter(Feedback.id == int(args.feedback_id)).first()
        if fb is None:
            fb = session.query(Feedback).filter(Feedback.run_id == args.feedback_id).first()
        if fb is not None:
            run = session.query(Run).filter(Run.run_id == fb.run_id).first()
            if run is not None:
                request_text = run.request_masked
            origin = f"feedback {fb.id} ({fb.thumb}): {fb.comment}"

    golden = ROOT / "evals" / "golden_set.json"
    cases = json.loads(golden.read_text(encoding="utf-8"))
    new_id = f"gold_flywheel_{len(cases) + 1:02d}"
    cases.append(
        {
            "id": new_id, "tier": args.tier, "kind": "correctness_template", "input": request_text,
            "profile": "student_a", "expect": args.reference, "origin": origin,
        }
    )
    golden.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"Appended '{new_id}' to evals/golden_set.json ({len(cases)} cases). Run `make gate` and watch it fail.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
