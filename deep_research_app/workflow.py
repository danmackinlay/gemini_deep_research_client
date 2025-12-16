"""High-level research orchestration with prompt templates."""

from dataclasses import dataclass
from typing import Optional, Callable

from deep_research_app.deep_research import DeepResearchClient, StreamCallback
from deep_research_app.storage import RunStorage
from deep_research_app.models import ResearchRun, InteractionStatus

# Prompt templates
INITIAL_RESEARCH_TEMPLATE = """Act as an expert research analyst using the Gemini Deep Research agent. Plan, search, read, and synthesize multiple sources to answer the following research query.

## Research Topic
{topic}

## Research Questions
{questions}

## Constraints
{constraints}

## Output Format
Produce a single, self-contained Markdown document with these sections:
1. Executive Summary (2-3 paragraphs)
2. Key Questions Addressed
3. Main Findings (with subsections as needed)
4. Evidence and Citations (use inline citations and a references section)
5. Limitations and Open Questions

Use Markdown tables where helpful for comparing data or sources.
Do not include commentary about your research process; output only the report.
"""

REVISION_TEMPLATE = """You previously produced a research report in the preceding interaction.

The user has provided the following feedback:
---
{feedback}
---

Please produce a complete revised Markdown report that:
1. Incorporates this feedback
2. Preserves correct facts and citations from the original
3. Maintains the same document structure unless the feedback requests changes
4. Clearly indicates any new research or sources added

Output only the revised report in Markdown format.
"""


@dataclass
class ResearchConstraints:
    """Constraints for a research run."""

    timeframe: Optional[str] = None
    region: Optional[str] = None
    depth: str = "comprehensive"
    max_words: Optional[int] = None
    focus_areas: Optional[list[str]] = None


class ResearchWorkflow:
    """Orchestrates research runs with streaming and persistence."""

    def __init__(self, storage: Optional[RunStorage] = None) -> None:
        self._client = DeepResearchClient()
        self._storage = storage or RunStorage()

    def run_initial_research(
        self,
        topic: str,
        questions: Optional[list[str]] = None,
        constraints: Optional[ResearchConstraints] = None,
        *,
        on_event: Optional[StreamCallback] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> ResearchRun:
        """
        Run initial research on a topic.

        Handles streaming with fallback to polling on failure.
        """
        constraints = constraints or ResearchConstraints()

        # Build prompt from template
        prompt = self._build_initial_prompt(topic, questions, constraints)

        # Create run object
        run = ResearchRun.new(prompt)
        run.status = InteractionStatus.RUNNING

        # Save initial state
        self._storage.save_run(run)

        # Wrap callback to track state for resume
        wrapped_callback = self._wrap_event_callback(run.run_id, on_event)

        # Start research with streaming
        result = self._client.start_research(
            prompt,
            stream=True,
            on_event=wrapped_callback,
        )

        run.interaction_id = result.interaction_id

        if result.complete_via_stream:
            # Streaming completed successfully
            run.report_markdown = result.final_markdown
            run.status = InteractionStatus.COMPLETED
            run.usage = result.usage
            self._storage.clear_stream_state(run.run_id)
        elif result.error and "Interrupted" in result.error:
            # User interrupted - state already saved via callback
            run.status = InteractionStatus.INTERRUPTED
        else:
            # Streaming failed or incomplete - fall back to polling
            if on_status:
                on_status("Streaming incomplete, falling back to polling...")

            poll_result = self._client.poll_interaction(
                result.interaction_id,
                on_status=on_status,
            )

            if poll_result.status == InteractionStatus.COMPLETED:
                run.report_markdown = poll_result.final_markdown
                run.status = InteractionStatus.COMPLETED
                run.usage = poll_result.usage
                self._storage.clear_stream_state(run.run_id)
            else:
                run.status = poll_result.status

        # Save final state
        self._storage.save_run(run)

        return run

    def revise_research(
        self,
        run_id: str,
        feedback: str,
        *,
        on_event: Optional[StreamCallback] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> ResearchRun:
        """
        Create a revision of an existing research run.

        Uses previous_interaction_id for context continuity.
        """
        # Load previous run
        previous_run = self._storage.load_latest_run(run_id)
        if not previous_run:
            raise ValueError(f"Run not found: {run_id}")

        if previous_run.status != InteractionStatus.COMPLETED:
            raise ValueError(
                f"Cannot revise incomplete run (status: {previous_run.status})"
            )

        # Build revision prompt
        revision_prompt = REVISION_TEMPLATE.format(feedback=feedback)

        # Create new run version
        run = previous_run.create_revision(feedback, revision_prompt)
        run.status = InteractionStatus.RUNNING

        # Save initial state
        self._storage.save_run(run)

        # Wrap callback
        wrapped_callback = self._wrap_event_callback(run.run_id, on_event)

        # Start revision with previous context
        result = self._client.start_research_with_context(
            prompt=revision_prompt,
            previous_interaction_id=previous_run.interaction_id,
            stream=True,
            on_event=wrapped_callback,
        )

        run.interaction_id = result.interaction_id

        # Same completion/fallback logic as initial research
        if result.complete_via_stream:
            run.report_markdown = result.final_markdown
            run.status = InteractionStatus.COMPLETED
            run.usage = result.usage
            self._storage.clear_stream_state(run.run_id)
        elif result.error and "Interrupted" in result.error:
            run.status = InteractionStatus.INTERRUPTED
        else:
            if on_status:
                on_status("Streaming incomplete, falling back to polling...")

            poll_result = self._client.poll_interaction(
                result.interaction_id,
                on_status=on_status,
            )

            if poll_result.status == InteractionStatus.COMPLETED:
                run.report_markdown = poll_result.final_markdown
                run.status = InteractionStatus.COMPLETED
                run.usage = poll_result.usage
                self._storage.clear_stream_state(run.run_id)
            else:
                run.status = poll_result.status

        self._storage.save_run(run)
        return run

    def resume_interrupted(
        self,
        run_id: str,
        *,
        on_event: Optional[StreamCallback] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> ResearchRun:
        """Resume an interrupted research run."""
        run = self._storage.load_latest_run(run_id)
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        stream_state = self._storage.load_stream_state(run_id)
        if not stream_state:
            raise ValueError(f"No saved stream state for run: {run_id}")

        # Wrap callback
        wrapped_callback = self._wrap_event_callback(run.run_id, on_event)

        result = self._client.resume_stream(
            stream_state["interaction_id"],
            stream_state["last_event_id"],
            on_event=wrapped_callback,
        )

        if result.complete_via_stream:
            # Combine partial text with resumed text
            partial = stream_state.get("partial_text", "")
            resumed = result.final_markdown or ""
            run.report_markdown = partial + resumed
            run.status = InteractionStatus.COMPLETED
            run.usage = result.usage
            self._storage.clear_stream_state(run_id)
        else:
            # Fall back to polling
            if on_status:
                on_status("Resume incomplete, falling back to polling...")

            poll_result = self._client.poll_interaction(
                result.interaction_id,
                on_status=on_status,
            )
            if poll_result.status == InteractionStatus.COMPLETED:
                run.report_markdown = poll_result.final_markdown
                run.status = InteractionStatus.COMPLETED
                run.usage = poll_result.usage
                self._storage.clear_stream_state(run_id)
            else:
                run.status = poll_result.status

        self._storage.save_run(run)
        return run

    def _build_initial_prompt(
        self,
        topic: str,
        questions: Optional[list[str]],
        constraints: ResearchConstraints,
    ) -> str:
        """Build the initial research prompt from template."""
        if questions:
            questions_text = "\n".join(f"- {q}" for q in questions)
        else:
            questions_text = f"- What is {topic}?"

        constraints_parts = []
        if constraints.timeframe:
            constraints_parts.append(f"- Time period: {constraints.timeframe}")
        if constraints.region:
            constraints_parts.append(f"- Geographic focus: {constraints.region}")
        if constraints.max_words:
            constraints_parts.append(f"- Maximum length: {constraints.max_words} words")
        if constraints.focus_areas:
            constraints_parts.append(
                f"- Focus areas: {', '.join(constraints.focus_areas)}"
            )
        constraints_parts.append(f"- Depth: {constraints.depth}")

        constraints_text = (
            "\n".join(constraints_parts) if constraints_parts else "None specified"
        )

        return INITIAL_RESEARCH_TEMPLATE.format(
            topic=topic,
            questions=questions_text,
            constraints=constraints_text,
        )

    def _wrap_event_callback(
        self,
        run_id: str,
        on_event: Optional[StreamCallback],
    ) -> StreamCallback:
        """Wrap callback to also save stream state periodically."""
        accumulated: dict = {"text": "", "last_event_id": None, "interaction_id": None}

        def wrapper(event_type: str, text: str) -> None:
            if event_type == "start" and "Interaction started:" in text:
                accumulated["interaction_id"] = text.split(": ")[1]
            if event_type == "text":
                accumulated["text"] += text

            # Save state periodically for resume capability
            if accumulated["interaction_id"] and accumulated.get("last_event_id"):
                self._storage.save_stream_state(
                    run_id,
                    accumulated["interaction_id"],
                    accumulated["last_event_id"],
                    accumulated["text"],
                )

            if on_event:
                on_event(event_type, text)

        return wrapper
