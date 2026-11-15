from codeforge.state import CodeForgeState


def request_with_context(state: CodeForgeState) -> str:
    """The (masked) request, prefixed by compacted conversation history when there is any."""
    req = state.get("raw_request_masked", "")
    history = state.get("history") or []
    if not history:
        return req
    lines = [f"- {t.get('role', 'user')}: {str(t.get('content', ''))[:300]}" for t in history]
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nCurrent request:\n{req}"
