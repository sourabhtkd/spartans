import json
from pathlib import Path

import pytest

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


def _run_pairs(cache: SemanticCache) -> tuple[int, int, list[str]]:
    pairs_path = Path(__file__).resolve().parent.parent.parent / "evals" / "cache_pairs.json"
    pairs = json.loads(pairs_path.read_text(encoding="utf-8"))
    assert len(pairs) >= 12
    hits = false_hits = 0
    missed = []
    for pair in pairs:
        cache.store(pair["q1"], code=f"def {pair['id']}(): pass", tests="def test(): pass")
        res = cache.lookup(pair["q2"])
        if res is not None and pair["expect"] == "miss":
            false_hits += 1
        elif res is not None:
            hits += 1
        elif pair["expect"] == "hit":
            missed.append(pair["id"])
    return hits, false_hits, missed


@pytest.mark.parametrize("backend", ["hash", "model"])
def test_semantic_cache_pairs_zero_false_hits(backend, monkeypatch):
    import codeforge.cost.semantic_cache as sc

    monkeypatch.setenv("CODEFORGE_EMBEDDINGS", backend)
    monkeypatch.setattr(sc, "_model", None)
    if backend == "model" and not sc._get_embedding_model():
        pytest.skip("embedding model unavailable")
    cache = SemanticCache(session_factory=init_db("sqlite:///:memory:"))
    hits, false_hits, missed = _run_pairs(cache)
    assert false_hits == 0  # the hard requirement, on every backend
    if backend == "model":
        assert hits >= 3, f"too few true hits; missed: {missed}"


def test_cache_is_role_and_policy_version_scoped(monkeypatch):
    cache = SemanticCache(session_factory=init_db("sqlite:///:memory:"))
    cache.store("write a palindrome checker", "code", "tests", role="student")
    assert cache.lookup("write a palindrome checker", role="student") is not None
    assert cache.lookup("write a palindrome checker", role="instructor") is None
    monkeypatch.setattr("codeforge.cost.semantic_cache.POLICY_VERSION", "999")
    assert cache.lookup("write a palindrome checker", role="student") is None


def test_cache_requires_context_free_and_respects_ttl_and_toggle(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from codeforge.store.db import CacheEntry

    sf = init_db("sqlite:///:memory:")
    cache = SemanticCache(session_factory=sf)
    cache.store("write a palindrome checker", "code", "tests")
    assert cache.lookup("write a palindrome checker", context_free=False) is None
    with sf() as s:
        e = s.query(CacheEntry).one()
        e.created_at = datetime.now(UTC) - timedelta(hours=48)
        s.commit()
    assert cache.lookup("write a palindrome checker") is None  # expired (TTL 24h)
    cache.store("write a palindrome checker", "code", "tests")
    monkeypatch.setenv("CODEFORGE_CACHE_ENABLED", "0")
    assert cache.lookup("write a palindrome checker") is None


def test_cache_key_is_stable_and_guard_tokens_canonical():
    from codeforge.cost.semantic_cache import make_cache_key

    assert make_cache_key("Sort  list", "student") == make_cache_key("sort list", "student")
    assert make_cache_key("sort list", "student") != make_cache_key("sort list", "instructor")
    assert extract_guard_tokens("find the largest") == extract_guard_tokens("find the maximum")
    assert extract_guard_tokens("sum of even numbers") != extract_guard_tokens("sum of odd numbers")


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
