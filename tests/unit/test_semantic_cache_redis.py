import fakeredis
import pytest

import codeforge.cost.semantic_cache as sc
from codeforge.config import clear_settings_cache
from codeforge.cost.semantic_cache import SemanticCache
from codeforge.store.db import init_db


@pytest.fixture
def redis_cache(monkeypatch):
    monkeypatch.setenv("CODEFORGE_EMBEDDINGS", "hash")
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/1")
    clear_settings_cache()
    monkeypatch.setattr(sc, "_model", None)
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(sc, "_get_redis", lambda: client)
    yield SemanticCache(session_factory=init_db("sqlite:///:memory:")), client
    clear_settings_cache()


def test_store_and_lookup_hit_with_ttl(redis_cache):
    cache, client = redis_cache
    cache.store("write a palindrome checker", "code", "tests")
    keys = list(client.scan_iter("cf:cache:*"))
    assert len(keys) == 1 and 0 < client.ttl(keys[0]) <= 24 * 3600
    assert cache.lookup("write a palindrome checker")["code"] == "code"


def test_role_and_guard_tokens_scope_redis_entries(redis_cache):
    cache, _ = redis_cache
    cache.store("sort a list in ascending order", "code", "tests")
    assert cache.lookup("sort a list in ascending order", role="instructor") is None
    assert cache.lookup("sort a list in descending order") is None


def test_expired_entry_is_a_miss(redis_cache):
    cache, client = redis_cache
    cache.store("write a palindrome checker", "code", "tests")
    for key in client.scan_iter("cf:cache:*"):
        client.delete(key)  # what Redis does when the TTL elapses
    assert cache.lookup("write a palindrome checker") is None


def test_redis_down_falls_back_to_sqlite(monkeypatch):
    monkeypatch.setenv("CODEFORGE_EMBEDDINGS", "hash")
    clear_settings_cache()
    monkeypatch.setattr(sc, "_model", None)

    class Broken:
        def __getattr__(self, name):
            raise ConnectionError("redis down")

    monkeypatch.setattr(sc, "_get_redis", lambda: Broken())
    cache = SemanticCache(session_factory=init_db("sqlite:///:memory:"))
    cache.store("write a palindrome checker", "code", "tests")
    assert cache.lookup("write a palindrome checker")["code"] == "code"
