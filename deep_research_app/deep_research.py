"""Core Gemini Deep Research API client with polling-based execution.

Architecture Decision: Poll-Only Design
========================================

This client uses polling rather than streaming for the Gemini Deep Research API.
We removed streaming support because:

1. Non-trivial research queries reliably timeout the streaming connection
   (gateway_timeout after ~60 seconds, while research takes 3-10 minutes)

2. The polling fallback was already required for all real research queries,
   making streaming code pure complexity with no practical benefit

3. Resume functionality works better with interaction_id polling than
   event-based stream checkpointing

The workflow is now:
1. create_interaction() - Start background research, get interaction_id
2. poll_interaction() - Wait for completion, get results

This simplifies the codebase significantly while maintaining all functionality.
"""

import base64
import mimetypes
import time
from collections.abc import Callable
from typing import Any

from google import genai

from deep_research_app.config import get_settings
from deep_research_app.models import (
    InteractionStatus,
    PollResult,
    ReportImage,
    UsageMetadata,
)

# Type alias for debug callback: (chunk_dict) -> None
DebugCallback = Callable[[dict[str, Any]], None]

# API status -> our status. "incomplete" and "budget_exceeded" are terminal
# failures. "requires_action" only arises with collaborative planning or
# function calling, neither of which we enable, so it is deliberately absent.
_STATUS_MAP = {
    "queued": InteractionStatus.PENDING,
    "in_progress": InteractionStatus.RUNNING,
    "completed": InteractionStatus.COMPLETED,
    "failed": InteractionStatus.FAILED,
    "cancelled": InteractionStatus.CANCELLED,
    "incomplete": InteractionStatus.FAILED,
    "budget_exceeded": InteractionStatus.FAILED,
}


def _map_status(api_status: str) -> InteractionStatus:
    assert api_status in _STATUS_MAP, f"Unexpected interaction status: {api_status!r}"
    return _STATUS_MAP[api_status]


def _report_content(
    interaction, figure_stem: str = "fig"
) -> tuple[str | None, list[ReportImage]]:
    """Extract the report from the final model_output step.

    Images become `![Figure N]({figure_stem}N.ext)` links at their position in
    the text; the image bytes are returned for the caller to save alongside.
    """
    outputs = [s for s in interaction.steps or [] if s.type == "model_output"]
    if not outputs:
        return None, []
    parts: list[str] = []
    images: list[ReportImage] = []
    for c in outputs[-1].content or []:
        if c.type == "text":
            parts.append(c.text)
        elif c.type == "image":
            assert c.data, "Image content without inline data is unsupported"
            ext = mimetypes.guess_extension(c.mime_type)
            assert ext, f"Unknown image mime type: {c.mime_type!r}"
            n = len(images) + 1
            images.append(
                ReportImage(f"{figure_stem}{n}{ext}", base64.b64decode(c.data))
            )
            parts.append(f"![Figure {n}]({images[-1].filename})")
        else:
            raise AssertionError(f"Unexpected report content type: {c.type!r}")
    return "\n\n".join(parts) or None, images


class DeepResearchClient:
    """Client for Gemini Deep Research API using polling-based execution."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._agent = settings.agent_name
        self._agent_config = {
            "type": "deep-research",
            "thinking_summaries": settings.thinking_summaries,
            "visualization": settings.visualization,
        }

    def _extract_usage(self, interaction) -> UsageMetadata | None:
        """Extract UsageMetadata from an interaction object.

        The API may delay populating the usage field even after marking
        an interaction as completed. This helper centralizes the extraction
        logic for use in both polling and status checks.
        """
        u = interaction.usage
        if not u:
            return None
        searches = sum(
            g.count or 0
            for g in u.grounding_tool_count or []
            if g.type == "google_search"
        )
        return UsageMetadata(
            prompt_tokens=u.total_input_tokens or 0,
            output_tokens=u.total_output_tokens or 0,
            total_tokens=u.total_tokens or 0,
            thinking_tokens=u.total_thought_tokens or 0,
            cached_tokens=u.total_cached_tokens or 0,
            tool_use_tokens=u.total_tool_use_tokens or 0,
            search_queries=searches,
        )

    def create_interaction(
        self,
        prompt: str,
        *,
        on_debug: DebugCallback | None = None,
    ) -> str:
        """
        Create a new Deep Research interaction.

        Starts background research and returns the interaction_id immediately.
        Use poll_interaction() to wait for completion and get results.

        Args:
            prompt: The research query/topic
            on_debug: Optional callback for debugging interaction creation

        Returns:
            interaction_id for polling
        """
        response = self._client.interactions.create(
            input=prompt,
            agent=self._agent,
            background=True,
            stream=False,
            agent_config=self._agent_config,
        )

        if on_debug:
            on_debug({"event": "interaction_created", "id": response.id})

        return response.id

    def create_interaction_with_context(
        self,
        prompt: str,
        previous_interaction_id: str,
        *,
        on_debug: DebugCallback | None = None,
    ) -> str:
        """
        Create a new interaction with context from a previous one.

        Used for revisions that need context from prior research.

        Args:
            prompt: The revision prompt
            previous_interaction_id: ID of the interaction to use as context
            on_debug: Optional callback for debugging

        Returns:
            interaction_id for polling
        """
        response = self._client.interactions.create(
            input=prompt,
            agent=self._agent,
            background=True,
            stream=False,
            previous_interaction_id=previous_interaction_id,
            agent_config=self._agent_config,
        )

        if on_debug:
            on_debug(
                {
                    "event": "interaction_created",
                    "id": response.id,
                    "previous_id": previous_interaction_id,
                }
            )

        return response.id

    def poll_interaction(
        self,
        interaction_id: str,
        *,
        interval: float | None = None,
        timeout: float | None = None,
        on_status: Callable[[str], None] | None = None,
        figure_stem: str = "fig",
    ) -> PollResult:
        """
        Poll an interaction until completion or timeout.

        Args:
            interaction_id: The interaction to poll
            interval: Seconds between polls (default from settings)
            timeout: Max seconds to wait (default from settings)
            on_status: Optional callback for status updates
            figure_stem: Filename prefix for figures linked from the report

        Returns:
            PollResult with status, final_markdown, images, and usage
        """
        settings = get_settings()
        interval = interval or settings.default_poll_interval
        timeout = timeout or settings.default_poll_timeout
        start_time = time.time()

        while True:
            try:
                interaction = self._client.interactions.get(interaction_id)
                status = interaction.status
                mapped = _map_status(status)

                if on_status:
                    elapsed = time.time() - start_time
                    on_status(f"Status: {status} ({elapsed:.0f}s)")

                if mapped == InteractionStatus.COMPLETED:
                    text, images = _report_content(interaction, figure_stem)
                    return PollResult(
                        interaction_id=interaction_id,
                        status=InteractionStatus.COMPLETED,
                        final_markdown=text,
                        usage=self._extract_usage(interaction),
                        images=images,
                    )

                if mapped in (InteractionStatus.FAILED, InteractionStatus.CANCELLED):
                    detail = f": {interaction.error}" if interaction.error else ""
                    return PollResult(
                        interaction_id=interaction_id,
                        status=mapped,
                        final_markdown=None,
                        error=f"Interaction {status}{detail}",
                    )

                if timeout and (time.time() - start_time) > timeout:
                    return PollResult(
                        interaction_id=interaction_id,
                        status=InteractionStatus.RUNNING,
                        final_markdown=None,
                        error="Polling timeout exceeded",
                    )

                time.sleep(interval)

            except KeyboardInterrupt:
                return PollResult(
                    interaction_id=interaction_id,
                    status=InteractionStatus.INTERRUPTED,
                    final_markdown=None,
                    error="Interrupted by user",
                )

    def get_interaction_status(
        self, interaction_id: str
    ) -> tuple[InteractionStatus, str | None, UsageMetadata | None]:
        """
        Get current status of an interaction (single poll).

        Returns (status, final_text_if_complete, usage_if_available)
        """
        interaction = self._client.interactions.get(interaction_id)
        status = _map_status(interaction.status)

        final_text = None
        if status == InteractionStatus.COMPLETED:
            final_text, _ = _report_content(interaction)

        usage = self._extract_usage(interaction)

        return status, final_text, usage
