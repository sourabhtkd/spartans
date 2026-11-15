"""What every LLM-backed agent shares: its system prompt, message building and tier bookkeeping.

An agent is a small class: `__call__(state)` asks the model for a structured answer and returns
the state fields it wants to change. Each agent imports `resilient_invoke` itself (tests patch it
per agent) and decides what to do when every model is down (`AllModelsDown`).
"""
from functools import cache
from pathlib import Path

from codeforge.state import CodeForgeState, worst_tier

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    """Read a system prompt once per process (agents are instantiated per node call)."""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def request_with_context(state: CodeForgeState) -> str:
    """The (masked) request, prefixed by compacted conversation history when there is any."""
    request = state.get("request_masked", "")
    history = state.get("history") or []
    if not history:
        return request
    # Assistant turns carry the previously generated code, which follow-ups need, so they get more room.
    lines = [
        f"- {turn.get('role', 'user')}: {str(turn.get('content', ''))[: 1200 if turn.get('role') == 'assistant' else 300]}"
        for turn in history
    ]
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nCurrent request:\n{request}"


class BaseAgent:
    name = ""  # also the model-tier key in config.yaml and the prompt file name (prompts/<name>.md)

    def __init__(self) -> None:
        self.system_prompt = load_prompt(self.name)

    def messages(self, user_content: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]

    @staticmethod
    def served_by(state: CodeForgeState, tier: str) -> str:
        """The most degraded tier used so far in this run, including `tier`."""
        return worst_tier(state.get("served_by"), tier)
