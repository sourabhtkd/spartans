import argparse
import sys

from codeforge.config import current_identity
from codeforge.entrypoint import new_thread_id, submit


def main() -> None:
    parser = argparse.ArgumentParser(description="CodeForge CLI")
    parser.add_argument("request", nargs="?", help="Natural language programming request")
    parser.add_argument("--thread-id", default="", help="Thread ID for the conversation run")
    args = parser.parse_args()

    if not args.request:
        print("Usage: python -m codeforge.cli 'Write a palindrome function'")
        sys.exit(1)

    thread_id = args.thread_id or new_thread_id()
    identity = current_identity()

    print(f"CodeForge running as [{identity.user_id}] ({identity.role}) | Thread: {thread_id}")

    result = submit(args.request, thread_id=thread_id)

    if result.pending_approval:
        policy = result.values.get("policy")
        reasons = "; ".join(policy.reasons) if policy else "Risky code"
        print("\n⏸ Execution paused: Code requires reviewer approval.")
        print(f"Thread ID: {thread_id}")
        print(f"Reasons: {reasons}")
        print("Review in the Reviewer Queue page or run approval resume.")
        return

    report = result.values.get("report", "No report generated.")
    print("\n" + report)


if __name__ == "__main__":
    main()
