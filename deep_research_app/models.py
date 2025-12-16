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
class StreamState:
    """Tracks state during streaming for resume support."""

    interaction_id: str
    last_event_id: Optional[str] = None
    accumulated_text: str = ""
    thought_summaries: list[str] = field(default_factory=list)
    complete: bool = False
    error: Optional[str] = None


@dataclass
class StartResult:
    """Result from starting a new research interaction."""

    interaction_id: str
    last_event_id: Optional[str]
    final_markdown: Optional[str]
    complete_via_stream: bool
    error: Optional[str] = None


@dataclass
class ResumeResult:
    """Result from resuming an interrupted stream."""

    interaction_id: str
    last_event_id: Optional[str]
    final_markdown: Optional[str]
    complete_via_stream: bool
    error: Optional[str] = None


@dataclass
class PollResult:
    """Result from polling an interaction."""

    interaction_id: str
    status: InteractionStatus
    final_markdown: Optional[str]
    error: Optional[str] = None


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
