import argparse
import json
from pathlib import Path

from codeforge.store.db import Feedback, Run, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Flywheel: Convert feedback to new Golden Set case")
    parser.add_argument("feedback_id", help="ID or run_id of the feedback to promote")
    parser.add_argument("--tier", default="soft", choices=["hard", "soft"], help="Tier: hard or soft")
    args = parser.parse_args()

    session_factory = init_db()
    with session_factory() as session:
        # Check feedback by id or run_id
        fb = None
        if args.feedback_id.isdigit():
            fb = session.query(Feedback).filter(Feedback.id == int(args.feedback_id)).first()
        if not fb:
            fb = session.query(Feedback).filter(Feedback.run_id == args.feedback_id).first()

        req_text = "Prompt from feedback"
        if fb:
            run = session.query(Run).filter(Run.run_id == fb.run_id).first()
            if run:
                req_text = run.request_masked
            print(f"Promoting feedback (Run: {fb.run_id}, Thumb: {fb.thumb}, Comment: {fb.comment}) to golden set...")
        else:
            print(f"Creating golden case from identifier '{args.feedback_id}'...")
            req_text = args.feedback_id

    golden_file = Path("evals/golden_set.json")
    cases = json.loads(golden_file.read_text(encoding="utf-8"))

    new_id = f"gold_flywheel_{len(cases) + 1:02d}"
    new_case = {
        "id": new_id,
        "tier": args.tier,
        "kind": "correctness",
        "input": req_text,
        "profile": "student_a",
        "expect": "palindrome.py",
    }
    cases.append(new_case)
    golden_file.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"✅ Appended new case '{new_id}' to evals/golden_set.json (Total cases: {len(cases)})")


if __name__ == "__main__":
    main()
