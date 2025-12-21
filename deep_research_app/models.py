"""Data models for the Deep Research client."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class InteractionStatus(str, Enum):
    """Status of a Gemini interaction."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass
class UsageMetadata:
    """Token usage information from API response."""

    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    thinking_tokens: int = 0

    def calculate_cost(
        self,
        price_per_m_input: float = 2.0,
        price_per_m_output: float = 12.0,
    ) -> float:
        """Calculate cost in dollars based on token usage."""
        return (self.prompt_tokens / 1_000_000) * price_per_m_input + (
            self.output_tokens / 1_000_000
        ) * price_per_m_output

    def format_cost(self, include_total: bool = True) -> str:
        """Format usage with cost for display."""
        cost = self.calculate_cost()
        if include_total:
            return (
                f"Tokens: {self.prompt_tokens:,} in / {self.output_tokens:,} out / "
                f"{self.total_tokens:,} total | Cost: ${cost:.4f}"
            )
        return f"{self.prompt_tokens:,} in / {self.output_tokens:,} out | ${cost:.4f}"

    @classmethod
    def from_dict(cls, data: dict) -> "UsageMetadata":
        """Create UsageMetadata from a dict (e.g., from meta.json)."""
        return cls(
            prompt_tokens=data.get("prompt_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            thinking_tokens=data.get("thinking_tokens", 0),
        )


@dataclass
class StreamState:
    """Tracks state during streaming for resume support."""

    interaction_id: str
    last_event_id: Optional[str] = None
    accumulated_text: str = ""
    thought_summaries: list[str] = field(default_factory=list)
    complete: bool = False
    error: Optional[str] = None
    usage: Optional[UsageMetadata] = None


@dataclass
class StartResult:
    """Result from starting a new research interaction."""

    interaction_id: str
    last_event_id: Optional[str]
    final_markdown: Optional[str]
    complete_via_stream: bool
    error: Optional[str] = None
    usage: Optional[UsageMetadata] = None


@dataclass
class ResumeResult:
    """Result from resuming an interrupted stream."""

    interaction_id: str
    last_event_id: Optional[str]
    final_markdown: Optional[str]
    complete_via_stream: bool
    error: Optional[str] = None
    usage: Optional[UsageMetadata] = None


@dataclass
class PollResult:
    """Result from polling an interaction."""

    interaction_id: str
    status: InteractionStatus
    final_markdown: Optional[str]
    error: Optional[str] = None
    usage: Optional[UsageMetadata] = None


@dataclass
class ResearchRun:
    """Represents a versioned research run (local entity)."""

    run_id: str
    interaction_id: str
    version: int
    prompt_text: str
    report_markdown: Optional[str]
    created_at: datetime
    feedback: Optional[str] = None
    previous_interaction_id: Optional[str] = None
    status: InteractionStatus = InteractionStatus.PENDING
    usage: Optional[UsageMetadata] = None

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
        )


@dataclass
class RunMetadata:
    """Metadata stored in meta.json for a run directory."""

    run_id: str
    topic: str
    created_at: str
    versions: list[dict]
    latest_version: int
