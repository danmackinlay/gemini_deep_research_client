"""Configuration management for the Deep Research client."""

from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    gemini_api_key: str
    runs_dir: Path = Path("runs")
    agent_name: str = "deep-research-pro-preview-12-2025"
    default_poll_interval: float = 10.0
    default_poll_timeout: float = 1800.0  # 30 minutes
    thinking_summaries: str = "auto"  # "auto", "on", "off"

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
