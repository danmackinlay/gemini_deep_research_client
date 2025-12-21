"""Configuration management for the Deep Research client."""

from pathlib import Path
from functools import lru_cache

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Deep Research uses Gemini 3 Pro internally
# See: https://ai.google.dev/gemini-api/docs/deep-research
# Pricing per 1M tokens (as of Dec 2024)
PRICE_PER_M_INPUT = 2.0  # $2 per 1M input tokens
PRICE_PER_M_OUTPUT = 12.0  # $12 per 1M output tokens
# FIXME: Verify current rates and update if changed


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""


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
    try:
        return Settings()
    except ValidationError as e:
        missing = [err["loc"][0] for err in e.errors() if err["type"] == "missing"]
        if "gemini_api_key" in missing:
            raise ConfigurationError(
                "GEMINI_API_KEY environment variable is not set.\n\n"
                "Set it using one of:\n"
                "  export GEMINI_API_KEY='your-api-key'\n"
                "  echo 'export GEMINI_API_KEY=\"your-key\"' > .envrc && direnv allow\n"
                "  echo 'GEMINI_API_KEY=your-key' > .env"
            ) from None
        raise
