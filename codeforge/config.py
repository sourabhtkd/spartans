from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Identity(BaseModel, frozen=True):
    user_id: str
    role: Literal["student", "instructor"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    codeforge_profile: str | None = None
    groq_api_key: str = ""
    fallback_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    chaos_failure_rate: float = 0.0
    total_outage: bool = False

    active_profile: str = "student_a"
    profiles: dict[str, Identity] = {}
    quotas: dict[str, dict[str, int]] = {}
    models: dict[str, dict[str, Any]] = {}
    agent_tiers: dict[str, str] = {}
    sandbox: dict[str, Any] = {}
    approval: dict[str, Any] = {}
    cache: dict[str, Any] = {}
    resilience: dict[str, Any] = {}
    storage: dict[str, Any] = {}
    compaction: dict[str, Any] = {}

    @classmethod
    def load(cls, config_path: Path | str = "config.yaml") -> "Settings":
        config_file = Path(config_path)
        yaml_data: dict[str, Any] = {}
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    yaml_data = loaded

        # Convert yaml profiles into Identity objects if needed
        profiles_raw = yaml_data.get("profiles", {})
        profiles: dict[str, Identity] = {}
        for name, data in profiles_raw.items():
            if isinstance(data, dict):
                profiles[name] = Identity(**data)
            elif isinstance(data, Identity):
                profiles[name] = data
        yaml_data["profiles"] = profiles

        # Instantiate settings with env values taking precedence for env vars
        settings = cls(**yaml_data)
        if settings.codeforge_profile:
            settings.active_profile = settings.codeforge_profile
        return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def current_identity() -> Identity:
    """Returns the current Identity based only on config and active profile."""
    settings = get_settings()
    profile_name = settings.active_profile
    if profile_name not in settings.profiles:
        raise ValueError(f"Active profile '{profile_name}' not defined in profiles")
    return settings.profiles[profile_name]
