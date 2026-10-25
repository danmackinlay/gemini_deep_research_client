"""Configuration management for the Deep Research client."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Agents and per-task cost estimates as of Oct 2026.
# See: https://ai.google.dev/gemini-api/docs/deep-research
# Shutdown schedule: https://ai.google.dev/gemini-api/docs/deprecations
DEEP_RESEARCH_AGENT = "deep-research-preview-04-2026"  # ~$1-3 per task
DEEP_RESEARCH_MAX_AGENT = "deep-research-max-preview-04-2026"  # ~$3-7 per task


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    gemini_api_key: str
    runs_dir: Path = Path("runs")
    agent_name: str = DEEP_RESEARCH_AGENT
    default_poll_interval: float = 10.0
    default_poll_timeout: float = 3600.0  # agent's max research time is 60 minutes
    thinking_summaries: Literal["auto", "none"] = "auto"
    # Agent-generated charts; saved as report_v{N}_fig{M}.* beside the report
    visualization: Literal["auto", "off"] = "auto"

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
