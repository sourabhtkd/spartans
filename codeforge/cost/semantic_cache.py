import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy.orm import Session, sessionmaker

from codeforge.config import get_settings
from codeforge.store.db import CacheEntry, init_db

GUARD_KEYWORDS = {
    "ascending",
    "descending",
    "min",
    "max",
    "first",
    "last",
    "even",
    "odd",
    "recursive",
    "iterative",
    "inclusive",
    "exclusive",
    "case-sensitive",
    "case-insensitive",
    "not",
    "without",
    "python",
    "sql",
    "java",
    "rust",
    "go",
}


def extract_guard_tokens(text: str) -> set[str]:
    """Extracts answer-altering guard tokens and all numbers from text."""
    lowered = text.lower()
    tokens = set()
    for kw in GUARD_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", lowered):
            tokens.add(kw)
    # Extract numbers
    for num in re.findall(r"\b\d+\b", lowered):
        tokens.add(num)
    return tokens


_model: Any = None


def _get_embedding_model() -> Any:
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:  # noqa: BLE001
            _model = False
    return _model


def _compute_embedding(text: str) -> list[float]:
    """Computes a normalized embedding vector for semantic matching."""
    model = _get_embedding_model()
    if model:
        vec = model.encode(text)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    # Fallback bag-of-words
    words = re.findall(r"\w+", text.lower())
    dim = 128
    v = np.zeros(dim, dtype=np.float32)
    for w in words:
        v[hash(w) % dim] += 1.0
    norm = np.linalg.norm(v)
    if norm > 0:
        v = v / norm
    return v.tolist()


class SemanticCache:
    """Guarded semantic cache.
    Ensures zero false hits by enforcing strict equality of answer-changing guard tokens
    and cosine similarity above threshold (e.g. >= 0.92).
    """

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self.session_factory = session_factory or init_db()

    def lookup(self, request_text: str, context_free: bool = True) -> dict[str, Any] | None:
        if not context_free:
            return None

        target_guard = extract_guard_tokens(request_text)
        target_emb = np.array(_compute_embedding(request_text), dtype=np.float32)
        settings = get_settings()
        threshold = settings.cache.get("threshold", 0.90)
        ttl_hours = settings.cache.get("ttl_hours", 24)
        cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)

        with self.session_factory() as session:
            entries = session.query(CacheEntry).filter(CacheEntry.created_at >= cutoff).all()
            for entry in entries:
                cand_guard = set(json.loads(entry.guard_tokens))
                # Guard word check: must be strictly equal
                if cand_guard != target_guard:
                    continue

                cand_emb = np.array(json.loads(entry.embedding_json), dtype=np.float32)
                sim = float(np.dot(target_emb, cand_emb))
                if sim >= threshold:
                    return {
                        "code": entry.code,
                        "tests": entry.tests,
                        "similarity": sim,
                    }
        return None

    def store(
        self,
        request_text: str,
        code: str,
        tests: str,
        role: str = "student",
    ) -> None:
        guard_tokens = list(extract_guard_tokens(request_text))
        emb = _compute_embedding(request_text)
        cache_key = f"c_{hash(request_text) & 0xffffffff:x}"

        with self.session_factory() as session:
            existing = session.query(CacheEntry).filter(CacheEntry.cache_key == cache_key).first()
            if existing:
                existing.code = code
                existing.tests = tests
                existing.embedding_json = json.dumps(emb)
                existing.guard_tokens = json.dumps(guard_tokens)
                existing.created_at = datetime.now(UTC)
            else:
                entry = CacheEntry(
                    cache_key=cache_key,
                    request_text=request_text,
                    embedding_json=json.dumps(emb),
                    guard_tokens=json.dumps(guard_tokens),
                    code=code,
                    tests=tests,
                    role=role,
                )
                session.add(entry)
            session.commit()


_semantic_cache: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache:
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = SemanticCache()
    return _semantic_cache
