import pytest


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Every test gets its own sqlite DB + checkpoint file; never touches real state."""
    monkeypatch.setenv("CODEFORGE_DB_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("CODEFORGE_CHECKPOINT_PATH", str(tmp_path / "ckpt.db"))
    import codeforge.cost.semantic_cache as sc

    monkeypatch.setattr(sc, "_semantic_cache", None)
    yield
