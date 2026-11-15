import pytest


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Every test gets its own sqlite DB + checkpoint file; never touches real state."""
    monkeypatch.setenv("CODEFORGE_DB_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("CODEFORGE_CHECKPOINT_PATH", str(tmp_path / "ckpt.db"))
    import codeforge.cost.semantic_cache as sc

    monkeypatch.setattr(sc, "_semantic_cache", None)
    monkeypatch.setenv("CODEFORGE_EMBEDDINGS", "hash")  # fast & offline; model-backed test opts out
    # Never let tests reach a real provider, even if a dev has keys in .env.
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("FALLBACK_API_KEY", "")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    from codeforge.config import clear_settings_cache
    from codeforge.llm import clear_llm_cache, reset_breakers

    clear_llm_cache()
    reset_breakers()
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def as_profile(monkeypatch):
    """Switch the active config profile (identity always comes from config)."""
    from codeforge.config import clear_settings_cache

    def _switch(name: str) -> None:
        monkeypatch.setenv("CODEFORGE_PROFILE", name)
        clear_settings_cache()

    return _switch
