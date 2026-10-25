"""Data models for the Deep Research client."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class InteractionStatus(str, Enum):
    """Status of a Gemini interaction."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


# Gemini 3.1 Pro list rates (prompts <= 200k), USD, as of Oct 2026.
# Both Deep Research agents run on 3.1 Pro; update these if the agent changes.
# See: https://ai.google.dev/gemini-api/docs/pricing
PRICE_PER_M_INPUT = 2.0
PRICE_PER_M_CACHED_INPUT = 0.20
PRICE_PER_M_OUTPUT = 12.0  # also applies to thought tokens
PRICE_PER_SEARCH = 14.0 / 1000


@dataclass
class UsageMetadata:
    """Token usage reported on a completed interaction.

    total_tokens = prompt + output + thinking + tool_use. cached_tokens is a
    subset of prompt_tokens.
    """

    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    thinking_tokens: int = 0
    cached_tokens: int = 0
    tool_use_tokens: int = 0
    search_queries: int = 0

    def calculate_cost(self) -> float:
        """Estimate cost in dollars at list rates.

        Upper bound: search queries are all charged, although the first 5,000
        per month (shared across Gemini 3+ models) are free.
        """
        # FIXME: tool_use_tokens are billed at the input rate as a guess; the
        # pricing page says search-retrieved tokens are excluded but URL-context
        # tokens are not, and usage does not split them.
        uncached = self.prompt_tokens - self.cached_tokens
        assert uncached >= 0, f"cached_tokens exceeds prompt_tokens: {self}"
        return (
            (uncached + self.tool_use_tokens) / 1e6 * PRICE_PER_M_INPUT
            + self.cached_tokens / 1e6 * PRICE_PER_M_CACHED_INPUT
            + (self.output_tokens + self.thinking_tokens) / 1e6 * PRICE_PER_M_OUTPUT
            + self.search_queries * PRICE_PER_SEARCH
        )

    def format_cost(self, include_total: bool = True) -> str:
        """Format usage with cost for display."""
        cost = self.calculate_cost()
        tokens = (
            f"{self.prompt_tokens:,} in ({self.cached_tokens:,} cached) / "
            f"{self.output_tokens + self.thinking_tokens:,} out+thought"
        )
        if include_total:
            return (
                f"Tokens: {tokens} / {self.total_tokens:,} total | "
                f"{self.search_queries} searches | Cost: ~${cost:.2f}"
            )
        return f"{tokens} | {self.search_queries} searches | ${cost:.2f}"

    @classmethod
    def from_dict(cls, data: dict) -> "UsageMetadata":
        """Create UsageMetadata from a dict (e.g., from meta.json)."""
        return cls(**data)


@dataclass
class SeedFile:
    """A seed document loaded from disk, ready to embed in a prompt."""

    filename: str
    content: str


@dataclass
class ReportImage:
    """An agent-generated figure, linked from the report markdown by filename."""

    filename: str
    data: bytes


@dataclass
class PollResult:
    """Result from polling an interaction."""

    interaction_id: str
    status: InteractionStatus
    final_markdown: str | None
    error: str | None = None
    usage: UsageMetadata | None = None
    images: list[ReportImage] = field(default_factory=list)


@dataclass
class ResearchConstraints:
    """Constraints for a research run."""

    timeframe: str | None = None
    region: str | None = None
    max_words: int | None = None
    focus_areas: list[str] | None = None

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        return {
            "timeframe": self.timeframe,
            "region": self.region,
            "max_words": self.max_words,
            "focus_areas": self.focus_areas,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResearchConstraints":
        """Deserialize from dict."""
        return cls(
            timeframe=d.get("timeframe"),
            region=d.get("region"),
            max_words=d.get("max_words"),
            focus_areas=d.get("focus_areas"),
        )

    @classmethod
    def from_user_input(
        cls,
        *,
        timeframe: str | None,
        region: str | None,
        max_words: float | None,
        focus: str | None,
    ) -> "ResearchConstraints":
        """Create from raw user input with normalization."""
        return cls(
            timeframe=timeframe.strip() if timeframe else None,
            region=region.strip() if region else None,
            max_words=int(max_words) if max_words else None,
            focus_areas=[a.strip() for a in focus.split(",") if a.strip()]
            if focus
            else None,
        )


@dataclass
class RunInputs:
    """Original user inputs for a research run (preserved across revisions)."""

    topic: str
    constraints: ResearchConstraints
    questions: list[str] | None = None

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        return {
            "topic": self.topic,
            "constraints": self.constraints.to_dict(),
            "questions": self.questions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RunInputs":
        """Deserialize from dict."""
        return cls(
            topic=d["topic"],
            constraints=ResearchConstraints.from_dict(d.get("constraints", {})),
            questions=d.get("questions"),
        )


@dataclass
class ResearchRun:
    """Represents a versioned research run (local entity)."""

    run_id: str
    interaction_id: str
    version: int
    prompt_text: str
    report_markdown: str | None
    created_at: datetime
    feedback: str | None = None
    previous_interaction_id: str | None = None
    status: InteractionStatus = InteractionStatus.PENDING
    usage: UsageMetadata | None = None
    inputs: RunInputs | None = None
    seed_filenames: list[str] | None = None
    error: str | None = None  # why polling stopped short; not persisted

    @classmethod
    def new(cls, prompt_text: str) -> "ResearchRun":
        """Factory for creating a new initial run."""
        return cls(
            run_id=str(uuid.uuid4())[:8],
            interaction_id="",
            version=1,
            prompt_text=prompt_text,
            report_markdown=None,
            created_at=datetime.now(),
        )

    def create_revision(self, feedback: str, new_prompt: str) -> "ResearchRun":
        """Factory for creating a revision from this run."""
        return ResearchRun(
            run_id=self.run_id,
            interaction_id="",
            version=self.version + 1,
            prompt_text=new_prompt,
            report_markdown=None,
            created_at=datetime.now(),
            feedback=feedback,
            previous_interaction_id=self.interaction_id,
            inputs=self.inputs,  # Preserve original inputs across revisions
        )


@dataclass
class RunMetadata:
    """Metadata stored in meta.json for a run directory."""

    run_id: str
    topic: str
    created_at: str
    versions: list[dict]
    latest_version: int
    filename_stem: str | None = None
