"""High-level research orchestration with prompt templates."""

import re
from dataclasses import dataclass
from typing import Optional, Callable

from deep_research_app.deep_research import (
    DeepResearchClient,
    StreamCallback,
    DebugCallback,
)
from deep_research_app.storage import RunStorage
from deep_research_app.models import ResearchRun, InteractionStatus
from deep_research_app.citations import process_report

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
4. Limitations and Open Questions

Use Markdown tables where helpful for comparing data or sources.
Do not include commentary about your research process; output only the report.

## Citation Contract (must follow exactly)
- Every factual claim must end with one or more inline citation links in this exact format:
  [1](URL), [2](URL)
  (The number in square brackets must be the clickable link itself.)
- Do NOT use "[cite: …]", "(cite …)", or any other citation syntax.
- At the end include a section exactly titled: "## Sources"
  Each entry must be numbered and include a clickable Markdown link:
  1. [Title](URL)
  2. [Title](URL)
- URLs must be explicit. Do not write just site names without URLs.
- If the only URL you have is a vertexaisearch redirect URL, still include it.
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

REVISION_WITH_CONSTRAINTS_TEMPLATE = """You previously produced a research report in the preceding interaction.

The user has provided the following feedback:
---
{feedback}
---

Additionally, apply these updated constraints to the revised report:
{constraints}

Please produce a complete revised Markdown report that:
1. Incorporates the feedback above
2. Applies the updated constraints
3. Preserves correct facts and citations from the original
4. Clearly indicates any new research or sources added

Output only the revised report in Markdown format.
"""


def parse_prompt(prompt_text: str) -> dict:
    """Extract structured fields from stored prompt text.

    The prompt follows the INITIAL_RESEARCH_TEMPLATE format with sections like:
    ## Research Topic
    ## Research Questions
    ## Constraints
    """
    result: dict = {
        "topic": "",
        "timeframe": None,
        "region": None,
        "max_words": None,
        "focus_areas": None,
    }

    # Extract topic between "## Research Topic" and "## Research Questions"
    topic_match = re.search(
        r"## Research Topic\s*\n(.*?)(?=\n## Research Questions|\n## Constraints|$)",
        prompt_text,
        re.DOTALL,
    )
    if topic_match:
        result["topic"] = topic_match.group(1).strip()

    # Extract constraints section
    constraints_match = re.search(
        r"## Constraints\s*\n(.*?)(?=\n## Output Format|$)", prompt_text, re.DOTALL
    )
    if constraints_match:
        constraints_text = constraints_match.group(1)

        # Parse individual constraints
        timeframe_match = re.search(r"Time period:\s*(.+)", constraints_text)
        if timeframe_match:
            result["timeframe"] = timeframe_match.group(1).strip()

        region_match = re.search(r"Geographic focus:\s*(.+)", constraints_text)
        if region_match:
            result["region"] = region_match.group(1).strip()

        max_words_match = re.search(r"Maximum length:\s*(\d+)", constraints_text)
        if max_words_match:
            result["max_words"] = int(max_words_match.group(1))

        focus_match = re.search(r"Focus areas:\s*(.+)", constraints_text)
        if focus_match:
            result["focus_areas"] = focus_match.group(1).strip()

    return result


@dataclass
class ResearchConstraints:
    """Constraints for a research run."""

    timeframe: Optional[str] = None
    region: Optional[str] = None
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
        on_debug: Optional[DebugCallback] = None,
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
            on_debug=on_debug,
        )

        run.interaction_id = result.interaction_id

        if result.complete_via_stream:
            # Streaming completed successfully
            run.report_markdown = self._process_citations(
                result.final_markdown, run.run_id, run.version, on_status
            )
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
                run.report_markdown = self._process_citations(
                    poll_result.final_markdown, run.run_id, run.version, on_status
                )
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
        constraints: Optional[ResearchConstraints] = None,
        *,
        on_event: Optional[StreamCallback] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_debug: Optional[DebugCallback] = None,
    ) -> ResearchRun:
        """
        Create a revision of an existing research run.

        Uses previous_interaction_id for context continuity.
        Optionally accepts updated constraints to apply to the revision.
        """
        # Load previous run
        previous_run = self._storage.load_latest_run(run_id)
        if not previous_run:
            raise ValueError(f"Run not found: {run_id}")

        if previous_run.status != InteractionStatus.COMPLETED:
            raise ValueError(
                f"Cannot revise incomplete run (status: {previous_run.status})"
            )

        # Build revision prompt - include constraints if provided
        if constraints:
            constraints_text = self._format_constraints(constraints)
            revision_prompt = REVISION_WITH_CONSTRAINTS_TEMPLATE.format(
                feedback=feedback, constraints=constraints_text
            )
        else:
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
            on_debug=on_debug,
        )

        run.interaction_id = result.interaction_id

        # Same completion/fallback logic as initial research
        if result.complete_via_stream:
            run.report_markdown = self._process_citations(
                result.final_markdown, run.run_id, run.version, on_status
            )
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
                run.report_markdown = self._process_citations(
                    poll_result.final_markdown, run.run_id, run.version, on_status
                )
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

    def _format_constraints(self, constraints: ResearchConstraints) -> str:
        """Format constraints as text for prompts."""
        parts = []
        if constraints.timeframe:
            parts.append(f"- Time period: {constraints.timeframe}")
        if constraints.region:
            parts.append(f"- Geographic focus: {constraints.region}")
        if constraints.max_words:
            parts.append(f"- Maximum length: {constraints.max_words} words")
        if constraints.focus_areas:
            parts.append(f"- Focus areas: {', '.join(constraints.focus_areas)}")
        return "\n".join(parts) if parts else "None specified"

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

        constraints_text = self._format_constraints(constraints)

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

    def _process_citations(
        self,
        report_text: Optional[str],
        run_id: str,
        version: int,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """Process citations in a report and save sources."""
        if not report_text:
            return report_text

        if on_status:
            on_status("Processing citations...")

        result = process_report(report_text, resolve_redirects=True)

        # Save sources
        if result.sources:
            self._storage.save_sources(run_id, version, result.sources)
            if on_status:
                on_status(f"Saved {len(result.sources)} sources")

        # Log any validation errors (but don't fail)
        if result.errors:
            if on_status:
                for error in result.errors:
                    on_status(f"Citation warning: {error}")

        return result.text
