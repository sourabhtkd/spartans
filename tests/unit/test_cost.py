import json
from pathlib import Path

from codeforge.cost.compaction import compact_history
from codeforge.cost.semantic_cache import SemanticCache, extract_guard_tokens
from codeforge.store.db import init_db


def test_guard_tokens_extraction():
    tokens1 = extract_guard_tokens("Sort elements in ascending order with 10 items")
    assert "ascending" in tokens1
    assert "10" in tokens1
    assert "descending" not in tokens1

    tokens2 = extract_guard_tokens("Sort elements in descending order with 10 items")
    assert "descending" in tokens2
    assert tokens1 != tokens2


def test_semantic_cache_pairs_evaluation():
    db_mem = init_db("sqlite:///:memory:")
    cache = SemanticCache(session_factory=db_mem)

    pairs_path = Path(__file__).resolve().parent.parent.parent / "evals" / "cache_pairs.json"
    pairs = json.loads(pairs_path.read_text(encoding="utf-8"))

    hits = 0
    false_hits = 0

    for pair in pairs:
        q1 = pair["q1"]
        q2 = pair["q2"]
        expect = pair["expect"]

        # Store q1
        cache.store(q1, code=f"def {pair['id']}(): pass", tests="def test(): pass")

        # Lookup q2
        res = cache.lookup(q2)
        if res is not None:
            if expect == "miss":
                false_hits += 1
            else:
                hits += 1
        else:
            if expect == "hit":
                # Missed an expected hit
                pass

    # Strictly 0 false hits required by Task 14
    assert false_hits == 0
    # At least 3 true hits required by Task 14
    assert hits >= 3


def test_history_compaction():
    history = [
        {"role": "user", "content": f"Turn {i} with key sk-1234567890abcdef1234"}
        for i in range(10)
    ]
    compacted = compact_history(history, max_turns=6, keep_last=4)
    # 1 summary turn + 4 recent turns = 5 turns
    assert len(compacted) == 5
    assert compacted[0]["role"] == "system"
    assert "Previous context summary" in compacted[0]["content"]
    # PII in older turns must be masked
    assert "sk-" not in compacted[0]["content"]
    assert "[SECRET]" in compacted[0]["content"]
    # Last turn preserved
    assert compacted[-1]["content"] == history[-1]["content"]
