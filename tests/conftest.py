"""Fixtures shared by tests/ and evals/ (evals/conftest.py re-exports them)."""
import pytest

from codeforge.runtime import reset_runtime_state


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Isolate every test: private sqlite files, no real API keys, no chaos, student profile."""
    env = {
        "CODEFORGE_DB_URL": f"sqlite:///{tmp_path}/test.db",
        "CODEFORGE_CHECKPOINT_PATH": str(tmp_path / "ckpt.db"),
        "CODEFORGE_EMBEDDINGS": "hash",  # fast and offline; the model-backed cache test opts out
        # Never reach a real provider or tracing backend, even if a developer has keys in .env.
        "GROQ_API_KEY": "",
        "FALLBACK_API_KEY": "",
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
        "CHAOS_FAILURE_RATE": "0",  # tests that want chaos opt in explicitly
        "TOTAL_OUTAGE": "false",
        "CODEFORGE_PROFILE": "student_a",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    reset_runtime_state()  # the next test's setup resets again, so no teardown is needed
    yield


@pytest.fixture
def as_profile(monkeypatch):
    """Switch the active config profile (identity always comes from config)."""

    def switch(name: str) -> None:
        monkeypatch.setenv("CODEFORGE_PROFILE", name)
        reset_runtime_state()

    return switch
