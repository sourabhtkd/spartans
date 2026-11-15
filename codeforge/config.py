"""All configuration in one place.

Values come from config.yaml, then environment variables / .env override them.
Identity (who the user is, and their role) comes ONLY from here, never from message text.

Environment variables are read when settings are first loaded; call `clear_settings_cache()`
after changing one at runtime (tests and benchmarks do).
"""
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Identity(BaseModel, frozen=True):
    user_id: str
    role: Literal["student", "instructor"]


class ModelConfig(BaseModel):
    provider: str = "groq"
    name: str = "llama-3.1-8b-instant"
    in_per_1k: float = 0.0  # USD per 1000 input tokens
    out_per_1k: float = 0.0  # USD per 1000 output tokens


class QuotaConfig(BaseModel):
    runs_per_hour: int = 20


class SandboxConfig(BaseModel):
    timeout_s: int = 10
    mem_mb: int = 512


class ApprovalConfig(BaseModel):
    expiry_minutes: float = 30


class CacheConfig(BaseModel):
    threshold: float = 0.92  # minimum cosine similarity for a semantic cache hit
    ttl_hours: float = 24


class ResilienceConfig(BaseModel):
    max_retries: int = 3
    base_s: float = 0.5  # first retry waits up to this long (full-jitter backoff)
    cap_s: float = 8.0  # backoff never waits longer than this
    breaker_failures: int = 5  # failures within the window that open the circuit breaker
    breaker_window_s: float = 60.0
    breaker_cooldown_s: float = 30.0


class StorageConfig(BaseModel):
    db_url: str = "sqlite:///codeforge.db"
    checkpoint_path: str = "checkpoints.db"


class CompactionConfig(BaseModel):
    max_turns: int = 6  # compact chat history once it is longer than this
    keep_last: int = 4  # ...keeping this many recent turns verbatim


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- secrets and switches (environment / .env) ---
    groq_api_key: str = ""
    fallback_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    chaos_failure_rate: float = 0.0  # fraction of model calls to fail on purpose (R5 demo)
    total_outage: bool = False  # fail every model call
    codeforge_profile: str | None = None  # overrides active_profile
    codeforge_cache_enabled: bool = True
    codeforge_embeddings: str = "model"  # "hash" = fast offline fallback (tests)
    codeforge_db_url: str | None = None  # overrides storage.db_url
    codeforge_checkpoint_path: str | None = None  # overrides storage.checkpoint_path
    sandbox_backend: str = "local"  # "docker" for container isolation
    sandbox_docker_image: str = "codeforge-sandbox"

    # --- config.yaml ---
    active_profile: str = "student_a"
    profiles: dict[str, Identity] = {}
    quotas: dict[str, QuotaConfig] = {}
    models: dict[str, ModelConfig] = {}
    agent_tiers: dict[str, str] = {}  # agent name -> key in `models`
    sandbox: SandboxConfig = SandboxConfig()
    approval: ApprovalConfig = ApprovalConfig()
    cache: CacheConfig = CacheConfig()
    resilience: ResilienceConfig = ResilienceConfig()
    storage: StorageConfig = StorageConfig()
    compaction: CompactionConfig = CompactionConfig()

    @classmethod
    def load(cls, config_path: Path | str = "config.yaml") -> "Settings":
        yaml_data: dict[str, Any] = {}
        config_file = Path(config_path)
        if config_file.exists():
            loaded = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                yaml_data = loaded
        settings = cls(**yaml_data)
        if settings.codeforge_profile:
            settings.active_profile = settings.codeforge_profile
        return settings

    def model_for(self, tier: str) -> ModelConfig:
        """Model settings for a tier name ('small', 'large', 'fallback'); defaults if not configured."""
        return self.models.get(tier, ModelConfig())

    @property
    def db_url(self) -> str:
        return self.codeforge_db_url or self.storage.db_url

    @property
    def checkpoint_path(self) -> str:
        return self.codeforge_checkpoint_path or self.storage.checkpoint_path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def current_identity() -> Identity:
    """The acting user, taken only from the active config profile."""
    settings = get_settings()
    profile_name = settings.active_profile
    if profile_name not in settings.profiles:
        raise ValueError(f"Active profile '{profile_name}' not defined in profiles")
    return settings.profiles[profile_name]
