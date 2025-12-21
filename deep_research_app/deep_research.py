"""Core Gemini Deep Research API client with streaming and polling support."""

import time
from typing import Any, Optional, Callable

from google import genai

from deep_research_app.config import get_settings
from deep_research_app.models import (
    StartResult,
    ResumeResult,
    PollResult,
    StreamState,
    InteractionStatus,
    UsageMetadata,
)

# Type alias for streaming event callback: (event_type, text) -> None
StreamCallback = Callable[[str, str], None]

# Type alias for debug callback: (chunk_dict) -> None
DebugCallback = Callable[[dict[str, Any]], None]


def _serialize_chunk(chunk: Any) -> dict[str, Any]:
    """Serialize a streaming chunk to a dict for debugging."""
    result: dict[str, Any] = {"event_type": getattr(chunk, "event_type", None)}

    if hasattr(chunk, "event_id"):
        result["event_id"] = chunk.event_id

    if hasattr(chunk, "interaction") and chunk.interaction:
        interaction = chunk.interaction
        result["interaction"] = {
            "id": getattr(interaction, "id", None),
            "status": getattr(interaction, "status", None),
        }
        if hasattr(interaction, "outputs") and interaction.outputs:
            result["interaction"]["outputs"] = []
            for output in interaction.outputs:
                output_dict: dict[str, Any] = {
                    "text_length": len(output.text)
                    if hasattr(output, "text") and output.text
                    else 0
                }
                if hasattr(output, "annotations") and output.annotations:
                    output_dict["annotations"] = [
                        {
                            "start_index": getattr(ann, "start_index", None),
                            "end_index": getattr(ann, "end_index", None),
                            "source": getattr(ann, "source", None),
                        }
                        for ann in output.annotations
                    ]
                result["interaction"]["outputs"].append(output_dict)
        if hasattr(interaction, "usage") and interaction.usage:
            u = interaction.usage
            result["interaction"]["usage"] = {
                "total_input_tokens": getattr(u, "total_input_tokens", None),
                "total_output_tokens": getattr(u, "total_output_tokens", None),
                "total_tokens": getattr(u, "total_tokens", None),
                "total_reasoning_tokens": getattr(u, "total_reasoning_tokens", None),
            }

    if hasattr(chunk, "delta") and chunk.delta:
        delta = chunk.delta
        result["delta"] = {
            "type": getattr(delta, "type", None),
        }
        if hasattr(delta, "text"):
            result["delta"]["text_length"] = len(delta.text) if delta.text else 0
        if hasattr(delta, "thought"):
            result["delta"]["thought_length"] = (
                len(delta.thought) if delta.thought else 0
            )
        if hasattr(delta, "annotations") and delta.annotations:
            result["delta"]["annotations"] = [
                {
                    "start_index": getattr(ann, "start_index", None),
                    "end_index": getattr(ann, "end_index", None),
                    "source": getattr(ann, "source", None),
                }
                for ann in delta.annotations
            ]

    # Capture any other attributes that might contain source info
    for attr in [
        "google_search_result",
        "url_context_result",
        "tool_use",
        "tool_result",
    ]:
        if hasattr(chunk, attr):
            val = getattr(chunk, attr)
            if val is not None:
                result[attr] = str(val)[:500]  # Truncate for readability

    return result


class DeepResearchClient:
    """Low-level client for Gemini Deep Research API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._agent = settings.agent_name
        self._thinking_summaries = settings.thinking_summaries

    def start_research(
        self,
        prompt: str,
        *,
        stream: bool = True,
        on_event: Optional[StreamCallback] = None,
        on_debug: Optional[DebugCallback] = None,
    ) -> StartResult:
        """
        Start a new Deep Research interaction.

        Args:
            prompt: The research query/topic
            stream: Whether to stream results (recommended)
            on_event: Callback for streaming events (event_type, text)
            on_debug: Callback for raw chunk debugging (receives serialized chunk dict)

        Returns:
            StartResult with interaction_id and optionally final_markdown
        """
        state = StreamState(interaction_id="")

        try:
            response = self._client.interactions.create(
                input=prompt,
                agent=self._agent,
                background=True,
                stream=stream,
                agent_config={
                    "type": "deep-research",
                    "thinking_summaries": self._thinking_summaries,
                },
            )

            if stream:
                for chunk in response:
                    if on_debug:
                        on_debug(_serialize_chunk(chunk))
                    self._process_chunk(chunk, state, on_event)

                    if state.complete:
                        break
            else:
                state.interaction_id = response.id

            # Fetch canonical interaction after completion for debugging
            if state.complete and on_debug:
                self._debug_canonical_fetch(state.interaction_id, on_debug)

            # If streaming completed but didn't include usage, fetch it
            if state.complete and state.usage is None:
                state.usage = self._fetch_usage(state.interaction_id)

            return StartResult(
                interaction_id=state.interaction_id,
                last_event_id=state.last_event_id,
                final_markdown=state.accumulated_text if state.complete else None,
                complete_via_stream=state.complete,
                error=state.error,
                usage=state.usage,
            )

        except KeyboardInterrupt:
            return StartResult(
                interaction_id=state.interaction_id,
                last_event_id=state.last_event_id,
                final_markdown=None,
                complete_via_stream=False,
                error="Interrupted by user",
            )

    def start_research_with_context(
        self,
        prompt: str,
        previous_interaction_id: str,
        *,
        stream: bool = True,
        on_event: Optional[StreamCallback] = None,
        on_debug: Optional[DebugCallback] = None,
    ) -> StartResult:
        """
        Start research with context from a previous interaction.

        Used for revisions that need context from prior research.
        """
        state = StreamState(interaction_id="")

        try:
            response = self._client.interactions.create(
                input=prompt,
                agent=self._agent,
                background=True,
                stream=stream,
                previous_interaction_id=previous_interaction_id,
                agent_config={
                    "type": "deep-research",
                    "thinking_summaries": self._thinking_summaries,
                },
            )

            if stream:
                for chunk in response:
                    if on_debug:
                        on_debug(_serialize_chunk(chunk))
                    self._process_chunk(chunk, state, on_event)

                    if state.complete:
                        break
            else:
                state.interaction_id = response.id

            if state.complete and on_debug:
                self._debug_canonical_fetch(state.interaction_id, on_debug)

            # If streaming completed but didn't include usage, fetch it
            if state.complete and state.usage is None:
                state.usage = self._fetch_usage(state.interaction_id)

            return StartResult(
                interaction_id=state.interaction_id,
                last_event_id=state.last_event_id,
                final_markdown=state.accumulated_text if state.complete else None,
                complete_via_stream=state.complete,
                error=state.error,
                usage=state.usage,
            )

        except KeyboardInterrupt:
            return StartResult(
                interaction_id=state.interaction_id,
                last_event_id=state.last_event_id,
                final_markdown=None,
                complete_via_stream=False,
                error="Interrupted by user",
            )

    def resume_stream(
        self,
        interaction_id: str,
        last_event_id: str,
        *,
        on_event: Optional[StreamCallback] = None,
    ) -> ResumeResult:
        """
        Resume an interrupted streaming interaction.

        Uses the documented resume endpoint with after=LAST_EVENT_ID.
        """
        state = StreamState(
            interaction_id=interaction_id,
            last_event_id=last_event_id,
        )

        try:
            response = self._client.interactions.get(
                id=interaction_id,
                stream=True,
                last_event_id=last_event_id,
            )

            for chunk in response:
                self._process_chunk(chunk, state, on_event)

                if state.complete:
                    break

            # If streaming completed but didn't include usage, fetch it
            if state.complete and state.usage is None:
                state.usage = self._fetch_usage(interaction_id)

            return ResumeResult(
                interaction_id=interaction_id,
                last_event_id=state.last_event_id,
                final_markdown=state.accumulated_text if state.complete else None,
                complete_via_stream=state.complete,
                error=state.error,
                usage=state.usage,
            )

        except KeyboardInterrupt:
            return ResumeResult(
                interaction_id=interaction_id,
                last_event_id=state.last_event_id,
                final_markdown=None,
                complete_via_stream=False,
                error="Interrupted by user",
            )

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

        Fallback for when streaming fails or for batch mode.
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
                    on_status(status)

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

    def _process_chunk(
        self,
        chunk,
        state: StreamState,
        on_event: Optional[StreamCallback],
    ) -> None:
        """Process a single streaming chunk and update state."""
        # Handle interaction.start - capture ID
        if chunk.event_type == "interaction.start":
            state.interaction_id = chunk.interaction.id
            if on_event:
                on_event("start", f"Interaction started: {state.interaction_id}")

        # Track last event ID for resume capability
        if hasattr(chunk, "event_id") and chunk.event_id:
            state.last_event_id = chunk.event_id

        # Handle content deltas
        if chunk.event_type == "content.delta":
            if hasattr(chunk, "delta"):
                delta = chunk.delta
                if hasattr(delta, "type"):
                    if delta.type == "text":
                        text = delta.text if hasattr(delta, "text") else ""
                        state.accumulated_text += text
                        if on_event:
                            on_event("text", text)
                    elif delta.type == "thought_summary":
                        thought = delta.thought if hasattr(delta, "thought") else ""
                        state.thought_summaries.append(thought)
                        if on_event:
                            on_event("thought", thought)

        # Handle completion
        if chunk.event_type == "interaction.complete":
            state.complete = True
            # Capture usage from completion event
            if hasattr(chunk, "interaction") and chunk.interaction:
                interaction = chunk.interaction
                if hasattr(interaction, "usage") and interaction.usage:
                    usage = interaction.usage
                    state.usage = UsageMetadata(
                        prompt_tokens=usage.total_input_tokens,
                        output_tokens=usage.total_output_tokens,
                        total_tokens=usage.total_tokens,
                        thinking_tokens=usage.total_reasoning_tokens,
                    )
            if on_event:
                on_event("complete", "")

        # Handle errors
        if chunk.event_type == "error":
            state.error = str(chunk)
            if on_event:
                on_event("error", state.error)

    def _fetch_usage(self, interaction_id: str) -> Optional[UsageMetadata]:
        """Fetch usage from canonical interaction if available."""
        try:
            interaction = self._client.interactions.get(interaction_id)
            if hasattr(interaction, "usage") and interaction.usage:
                u = interaction.usage
                return UsageMetadata(
                    prompt_tokens=u.total_input_tokens,
                    output_tokens=u.total_output_tokens,
                    total_tokens=u.total_tokens,
                    thinking_tokens=u.total_reasoning_tokens,
                )
        except Exception:
            pass
        return None

    def _debug_canonical_fetch(
        self,
        interaction_id: str,
        on_debug: DebugCallback,
    ) -> None:
        """Fetch canonical interaction and log for debugging."""
        try:
            interaction = self._client.interactions.get(interaction_id)
            result: dict[str, Any] = {
                "canonical_fetch": True,
                "id": interaction_id,
                "status": getattr(interaction, "status", None),
            }

            if hasattr(interaction, "outputs") and interaction.outputs:
                result["outputs"] = []
                for i, output in enumerate(interaction.outputs):
                    output_dict: dict[str, Any] = {
                        "index": i,
                        "text_length": len(output.text)
                        if hasattr(output, "text") and output.text
                        else 0,
                    }
                    # Capture annotations from canonical output
                    if hasattr(output, "annotations") and output.annotations:
                        output_dict["annotations"] = [
                            {
                                "start_index": getattr(ann, "start_index", None),
                                "end_index": getattr(ann, "end_index", None),
                                "source": getattr(ann, "source", None),
                            }
                            for ann in output.annotations
                        ]
                    result["outputs"].append(output_dict)

            # Check for any tool results or other data
            for attr in ["tool_results", "search_results", "url_context_results"]:
                if hasattr(interaction, attr):
                    val = getattr(interaction, attr)
                    if val:
                        result[attr] = str(val)[:2000]

            on_debug(result)
        except Exception as e:
            on_debug({"canonical_fetch": True, "error": str(e)})
