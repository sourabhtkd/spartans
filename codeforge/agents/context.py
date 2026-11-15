from functools import cache
from pathlib import Path

from codeforge.state import CodeForgeState

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    """Read a system prompt once per process (agents are instantiated per node call)."""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def request_with_context(state: CodeForgeState) -> str:
    """The (masked) request, prefixed by compacted conversation history when there is any."""
    req = state.get("raw_request_masked", "")
    history = state.get("history") or []
    if not history:
        return req
    lines = [f"- {t.get('role', 'user')}: {str(t.get('content', ''))[:300]}" for t in history]
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nCurrent request:\n{req}"
