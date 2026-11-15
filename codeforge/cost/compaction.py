from collections.abc import Callable
from typing import Any

from codeforge.guard.pii import mask_text


def _deterministic_summary(older: list[dict[str, Any]]) -> str:
    parts = []
    for turn in older:
        content = mask_text(str(turn.get("content", "")))
        parts.append(f"{turn.get('role', 'user')}: {content[:100]}")
    return "; ".join(parts)


def llm_summarize(older: list[dict[str, Any]]) -> str:
    """Summarise old turns with the small `compaction` tier model (inputs are already masked)."""
    from codeforge.llm import resilient_invoke
    from codeforge.state import HistorySummary

    text = "\n".join(f"{t.get('role', 'user')}: {mask_text(str(t.get('content', '')))[:400]}" for t in older)
    messages = [
        {"role": "system", "content": "Summarise this conversation in under 80 words. Keep requirements and decisions."},
        {"role": "user", "content": text},
    ]
    result, _ = resilient_invoke("compaction", messages, HistorySummary)
    return result.summary


def compact_history(
    history: list[dict[str, Any]],
    max_turns: int = 6,
    keep_last: int = 4,
    summarizer: Callable[[list[dict[str, Any]]], str] | None = None,
) -> list[dict[str, Any]]:
    """Condense older turns into one masked summary turn; keep the last K turns verbatim.

    Uses ``summarizer`` when given; any summarizer failure falls back to deterministic truncation.
    """
    if len(history) <= max_turns:
        return history
    older, recent = history[:-keep_last], history[-keep_last:]
    summary = ""
    if summarizer is not None:
        try:
            summary = mask_text(summarizer(older))
        except Exception:  # noqa: BLE001 - compaction must never break a request
            summary = ""
    if not summary:
        summary = _deterministic_summary(older)
    return [{"role": "system", "content": f"[Previous context summary: {summary}]"}, *recent]
