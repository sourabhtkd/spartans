"""Guarded semantic cache (R4).

A cached answer is reused only when ALL of these hold:
  * the request is context-free (no chat history),
  * role and policy version match (a cache hit must never cross a trust boundary),
  * the answer-changing "guard" tokens are IDENTICAL (ascending/descending, min/max, numbers, ...),
  * embedding cosine similarity >= threshold,
  * the entry is younger than the TTL.
A hit still goes through the test validator, policy gate and sandbox in the graph.
"""
import hashlib
import json
import re
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy.orm import Session, sessionmaker

from codeforge.config import get_settings
from codeforge.observability import get_logger
from codeforge.policy.code_policy import POLICY_VERSION
from codeforge.store.db import CacheEntry, init_db

logger = get_logger("semantic_cache")

# canonical guard token -> regex. Two requests may share a cache entry only if they
# trigger exactly the same set of these (plus the same numbers).
GUARD_PATTERNS: dict[str, str] = {
    "ascending": r"ascending|increasing",
    "descending": r"descending|decreasing",
    "min": r"min|minimum|smallest|lowest",
    "max": r"max|maximum|largest|biggest|greatest|highest",
    "first": r"first",
    "last": r"last",
    "even": r"even",
    "odd": r"odd",
    "recursive": r"recursive|recursion|recursively",
    "iterative": r"iterative|iteratively|iteration",
    "inclusive": r"inclusive",
    "exclusive": r"exclusive",
    "case-sensitive": r"case[- ]?sensitive",
    "case-insensitive": r"case[- ]?insensitive|ignor\w* case",
    "not": r"not|without|except|excluding|never",
    "remove": r"remove|delete|drop",
    "find": r"find|search|locate",
    "count": r"count|number of",
    "sum": r"sum|total",
    "product": r"product",
    "average": r"average|mean",
    "median": r"median",
    "reverse": r"reverse|backwards?",
    "upper": r"upper|uppercase|capitali[sz]e",
    "lower": r"lower|lowercase",
    "positive": r"positive",
    "negative": r"negative",
    "zero": r"zero",
    "empty": r"empty",
    "unique": r"unique|distinct",
    "duplicate": r"duplicates?|repeated",
    "all": r"all",
    "any": r"any",
    "sort": r"sort|sorted|sorting",
    "python": r"python",
    "sql": r"sql",
    "java": r"java",
    "javascript": r"javascript|js",
    "rust": r"rust",
    "go": r"go|golang",
    "c++": r"c\+\+",
    "typescript": r"typescript",
}
_COMPILED = {k: re.compile(rf"(?<![\w+]){v}(?![\w+])") for k, v in GUARD_PATTERNS.items()}


def extract_guard_tokens(text: str) -> set[str]:
    """Answer-altering guard tokens (canonicalised) plus every number in the text."""
    lowered = text.lower()
    tokens = {k for k, rx in _COMPILED.items() if rx.search(lowered)}
    tokens.update(re.findall(r"\b\d+\b", lowered))
    return tokens


def normalize_request(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def make_cache_key(text: str, role: str, policy_version: str = POLICY_VERSION) -> str:
    """Stable across processes (no Python hash())."""
    return hashlib.sha256(f"{normalize_request(text)}\x00{role}\x00{policy_version}".encode()).hexdigest()[:32]


_model: Any = None
_warned_fallback = False


def _get_embedding_model() -> Any:
    """Sentence-transformer model, or False if unavailable / disabled (CODEFORGE_EMBEDDINGS=hash)."""
    global _model, _warned_fallback
    if _model is None:
        if get_settings().codeforge_embeddings == "hash":
            _model = False
        else:
            try:
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception as exc:  # noqa: BLE001
                _model = False
                if not _warned_fallback:
                    _warned_fallback = True
                    logger.warning("embedding_model_unavailable_using_hash_fallback", error=str(exc))
    return _model


def _compute_embedding(text: str) -> list[float]:
    """Normalised embedding. Fallback is a stable hashed bag-of-words (zlib.crc32)."""
    model = _get_embedding_model()
    if model:
        vec = np.asarray(model.encode(text), dtype=np.float32)
    else:
        vec = np.zeros(256, dtype=np.float32)
        for w in re.findall(r"\w+", text.lower()):
            vec[zlib.crc32(w.encode()) % 256] += 1.0
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return [float(x) for x in vec]


def cache_enabled() -> bool:
    return get_settings().codeforge_cache_enabled


class SemanticCache:
    def __init__(self, session_factory: "sessionmaker[Session] | None" = None) -> None:
        self.session_factory = session_factory or init_db()

    def lookup(
        self, request_text: str, context_free: bool = True, role: str = "student"
    ) -> dict[str, Any] | None:
        if not context_free or not cache_enabled():
            return None
        cfg = get_settings().cache
        threshold = cfg.threshold
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=cfg.ttl_hours)
        target_guard = extract_guard_tokens(request_text)
        target_emb = np.asarray(_compute_embedding(request_text), dtype=np.float32)

        with self.session_factory() as session:
            entries = (
                session.query(CacheEntry)
                .filter(
                    CacheEntry.created_at >= cutoff,
                    CacheEntry.role == role,
                    CacheEntry.policy_version == POLICY_VERSION,
                )
                .all()
            )
            for entry in entries:
                if set(json.loads(str(entry.guard_tokens))) != target_guard:
                    continue
                cand = np.asarray(json.loads(str(entry.embedding_json)), dtype=np.float32)
                if cand.shape != target_emb.shape:
                    continue  # embedded with a different backend
                sim = float(np.dot(target_emb, cand))
                if sim >= threshold:
                    return {"code": entry.code, "tests": entry.tests, "similarity": sim}
        return None

    def store(self, request_text: str, code: str, tests: str, role: str = "student") -> None:
        if not cache_enabled():
            return
        key = make_cache_key(request_text, role)
        values = {
            "embedding_json": json.dumps(_compute_embedding(request_text)),
            "guard_tokens": json.dumps(sorted(extract_guard_tokens(request_text))),
            "code": code,
            "tests": tests,
            "role": role,
            "policy_version": POLICY_VERSION,
            "created_at": datetime.now(UTC),
        }
        with self.session_factory() as session:
            existing = session.get(CacheEntry, key)
            if existing:
                for k, v in values.items():
                    setattr(existing, k, v)
            else:
                session.add(CacheEntry(cache_key=key, **values))
            session.commit()


_semantic_cache: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache:
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = SemanticCache()
    return _semantic_cache
