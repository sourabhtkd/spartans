import argparse
import sys
import uuid

from codeforge.config import current_identity
from codeforge.graph import build_graph


def main() -> None:
    parser = argparse.ArgumentParser(description="CodeForge CLI")
    parser.add_argument("request", nargs="?", help="Natural language programming request")
    parser.add_argument("--thread-id", default="", help="Thread ID for the conversation run")
    args = parser.parse_args()

    if not args.request:
        print("Usage: python -m codeforge.cli 'Write a palindrome function'")
        sys.exit(1)

    thread_id = args.thread_id or f"run_{uuid.uuid4().hex[:8]}"
    identity = current_identity()

    print(f"CodeForge running as [{identity.user_id}] ({identity.role}) | Thread: {thread_id}")

    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "thread_id": thread_id,
        "raw_request_masked": args.request,
        "identity": identity,
    }

    # Execute graph
    for _ in graph.stream(initial_state, config=config):
        pass

    state = graph.get_state(config)

    # Check for interrupt
    if state.next and "human_approval" in state.next:
        policy = state.values.get("policy")
        reasons = "; ".join(policy.reasons) if policy else "Risky code"
        print("\n⏸ Execution paused: Code requires reviewer approval.")
        print(f"Thread ID: {thread_id}")
        print(f"Reasons: {reasons}")
        print("Review in Gradio Reviewer Queue or run approval resume.")
        return

    report = state.values.get("report", "No report generated.")
    print("\n" + report)


if __name__ == "__main__":
    main()
