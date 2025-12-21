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

import time
from typing import Any, Optional, Callable

from google import genai

from deep_research_app.config import get_settings
from deep_research_app.models import (
    PollResult,
    InteractionStatus,
    UsageMetadata,
)

# Type alias for debug callback: (chunk_dict) -> None
DebugCallback = Callable[[dict[str, Any]], None]


class DeepResearchClient:
    """Client for Gemini Deep Research API using polling-based execution."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._agent = settings.agent_name
        self._thinking_summaries = settings.thinking_summaries

    def create_interaction(
        self,
        prompt: str,
        *,
        on_debug: Optional[DebugCallback] = None,
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
            agent_config={
                "type": "deep-research",
                "thinking_summaries": self._thinking_summaries,
            },
        )

        if on_debug:
            on_debug({"event": "interaction_created", "id": response.id})

        return response.id

    def create_interaction_with_context(
        self,
        prompt: str,
        previous_interaction_id: str,
        *,
        on_debug: Optional[DebugCallback] = None,
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
            agent_config={
                "type": "deep-research",
                "thinking_summaries": self._thinking_summaries,
            },
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
        interval: Optional[float] = None,
        timeout: Optional[float] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> PollResult:
        """
        Poll an interaction until completion or timeout.

        Args:
            interaction_id: The interaction to poll
            interval: Seconds between polls (default from settings)
            timeout: Max seconds to wait (default from settings)
            on_status: Optional callback for status updates

        Returns:
            PollResult with status, final_markdown, and usage
        """
        settings = get_settings()
        interval = interval or settings.default_poll_interval
        timeout = timeout or settings.default_poll_timeout
        start_time = time.time()

        while True:
            try:
                interaction = self._client.interactions.get(interaction_id)
                status = interaction.status

                if on_status:
                    elapsed = time.time() - start_time
                    on_status(f"Status: {status} ({elapsed:.0f}s)")

                if status == "completed":
                    final_text = None
                    if interaction.outputs:
                        final_text = interaction.outputs[-1].text

                    # Capture usage from completed interaction
                    usage = None
                    if hasattr(interaction, "usage") and interaction.usage:
                        u = interaction.usage
                        usage = UsageMetadata(
                            prompt_tokens=u.total_input_tokens,
                            output_tokens=u.total_output_tokens,
                            total_tokens=u.total_tokens,
                            thinking_tokens=u.total_reasoning_tokens,
                        )

                    return PollResult(
                        interaction_id=interaction_id,
                        status=InteractionStatus.COMPLETED,
                        final_markdown=final_text,
                        usage=usage,
                    )

                if status in ("failed", "cancelled"):
                    return PollResult(
                        interaction_id=interaction_id,
                        status=(
                            InteractionStatus.FAILED
                            if status == "failed"
                            else InteractionStatus.CANCELLED
                        ),
                        final_markdown=None,
                        error=f"Interaction {status}",
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
    ) -> tuple[InteractionStatus, Optional[str]]:
        """
        Get current status of an interaction (single poll).

        Returns (status, final_text_if_complete)
        """
        interaction = self._client.interactions.get(interaction_id)
        status_str = interaction.status

        status_map = {
            "pending": InteractionStatus.PENDING,
            "running": InteractionStatus.RUNNING,
            "completed": InteractionStatus.COMPLETED,
            "failed": InteractionStatus.FAILED,
            "cancelled": InteractionStatus.CANCELLED,
        }
        status = status_map.get(status_str, InteractionStatus.RUNNING)

        final_text = None
        if status == InteractionStatus.COMPLETED and interaction.outputs:
            final_text = interaction.outputs[-1].text

        return status, final_text
