from typing import Any

from codeforge.guard.pii import mask_text


def compact_history(
    history: list[dict[str, Any]],
    max_turns: int = 6,
    keep_last: int = 4,
) -> list[dict[str, Any]]:
    """Compacts long chat histories to reduce token usage and cost.
    Condenses older turns into a single masked summary turn while preserving the last K turns verbatim.
    """
    if len(history) <= max_turns:
        return history

    older = history[:-keep_last]
    recent = history[-keep_last:]

    # Summarize older turns
    older_texts = []
    for turn in older:
        role = turn.get("role", "user")
        content = mask_text(str(turn.get("content", "")))
        older_texts.append(f"{role}: {content[:100]}")

    summary = "; ".join(older_texts)
    summary_turn = {
        "role": "system",
        "content": f"[Previous context summary: {summary}]",
    }
    return [summary_turn, *recent]
