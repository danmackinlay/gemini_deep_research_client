"""High-level research orchestration with prompt templates."""

from pathlib import Path
from typing import Optional, Callable

from deep_research_app.deep_research import (
    DeepResearchClient,
    DebugCallback,
)
from deep_research_app.storage import RunStorage
from deep_research_app.models import (
    ResearchRun,
    ResearchConstraints,
    RunInputs,
    SeedFile,
    InteractionStatus,
)
from deep_research_app.citations import process_report

EXT_TO_FENCE = {
    # Markdown family
    ".md": "markdown",
    ".markdown": "markdown",
    ".qmd": "markdown",
    ".rmd": "markdown",
    ".mdx": "markdown",
    # Typesetting / prose
    ".tex": "latex",
    ".rst": "rst",
    ".org": "org",
    ".typ": "typst",
    ".adoc": "asciidoc",
    ".asciidoc": "asciidoc",
    # Data / config
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".ini": "ini",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    # Bibliography / citations
    ".bib": "bibtex",
    ".cff": "yaml",
    ".ris": "",
    ".nbib": "",
    # Tabular / plain
    ".csv": "csv",
    ".tsv": "",
    ".txt": "",
}

# Prompt templates
INITIAL_RESEARCH_TEMPLATE = """Act as an expert research analyst using the Gemini Deep Research agent. Plan, search, read, and synthesize multiple sources to answer the following research query.

## Research Topic
{topic}

## Research Questions
{questions}

## Constraints
{constraints}
{seeds}
## Output Format
Begin the document with a single top-level `# Title` heading. The title must be a single descriptive phrase, 5–10 words long, suitable for use as a filename. Do not use colons (`:`), em-dashes (`—`), parentheses, or any subtitle separator. Prefer "A Survey of the Nash Program" over "The Nash Program: A Survey".

After the title, include these sections:
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
{seeds}
Please produce a complete revised Markdown report that:
1. Incorporates this feedback
2. Preserves correct facts and citations from the original
3. Maintains the same document structure unless the feedback requests changes
4. Clearly indicates any new research or sources added
5. Keeps the original `# Title` heading at the top (or, if the feedback materially changes the subject, replaces it with a new single-phrase title — no colons, em-dashes, or parentheses)

Output only the revised report in Markdown format.
"""

REVISION_WITH_CONSTRAINTS_TEMPLATE = """You previously produced a research report in the preceding interaction.

The user has provided the following feedback:
---
{feedback}
---

Additionally, apply these updated constraints to the revised report:
{constraints}
{seeds}
Please produce a complete revised Markdown report that:
1. Incorporates the feedback above
2. Applies the updated constraints
3. Preserves correct facts and citations from the original
4. Clearly indicates any new research or sources added
5. Keeps the original `# Title` heading at the top (or, if the feedback materially changes the subject, replaces it with a new single-phrase title — no colons, em-dashes, or parentheses)

Output only the revised report in Markdown format.
"""


class ResearchWorkflow:
    """Orchestrates research runs with polling-based execution."""

    def __init__(self, storage: Optional[RunStorage] = None) -> None:
        self._client = DeepResearchClient()
        self._storage = storage or RunStorage()

    def run_initial_research(
        self,
        topic: str,
        questions: Optional[list[str]] = None,
        constraints: Optional[ResearchConstraints] = None,
        seed_paths: Optional[list[Path]] = None,
        *,
        on_status: Optional[Callable[[str], None]] = None,
        on_debug: Optional[DebugCallback] = None,
    ) -> ResearchRun:
        """
        Run initial research on a topic.

        Creates a background interaction and polls until completion.
        """
        constraints = constraints or ResearchConstraints()

        # Stage seed files into the run directory (need run_id first)
        run = ResearchRun.new("")
        seed_filenames: list[str] = []
        if seed_paths:
            seed_filenames = self._storage.save_seeds(run.run_id, 1, seed_paths)
        seeds = self._storage.load_seeds(run.run_id, 1) if seed_filenames else []

        # Build prompt from template (now with seed contents)
        prompt = self._build_initial_prompt(topic, questions, constraints, seeds)
        run.prompt_text = prompt
        run.status = InteractionStatus.RUNNING
        run.inputs = RunInputs(
            topic=topic, constraints=constraints, questions=questions
        )
        run.seed_filenames = seed_filenames or None

        # Save initial state
        self._storage.save_run(run)

        # Create interaction
        if on_status:
            on_status("Starting research...")
        interaction_id = self._client.create_interaction(prompt, on_debug=on_debug)
        run.interaction_id = interaction_id

        # Save with interaction_id so we can resume if interrupted
        self._storage.save_run(run)

        # Poll until complete
        return self._poll_and_finalize(run, on_status)

    def revise_research(
        self,
        run_id: str,
        feedback: str,
        constraints: Optional[ResearchConstraints] = None,
        seed_paths: Optional[list[Path]] = None,
        *,
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

        new_version = previous_run.version + 1

        # Stage seed files for this revision (per-version, not inherited)
        seed_filenames: list[str] = []
        if seed_paths:
            seed_filenames = self._storage.save_seeds(run_id, new_version, seed_paths)
        seeds = self._storage.load_seeds(run_id, new_version) if seed_filenames else []
        seeds_block = self._format_seeds(seeds)

        # Build revision prompt - include constraints if provided
        if constraints:
            constraints_text = self._format_constraints(constraints)
            revision_prompt = REVISION_WITH_CONSTRAINTS_TEMPLATE.format(
                feedback=feedback,
                constraints=constraints_text,
                seeds=seeds_block,
            )
        else:
            revision_prompt = REVISION_TEMPLATE.format(
                feedback=feedback, seeds=seeds_block
            )

        # Create new run version
        run = previous_run.create_revision(feedback, revision_prompt)
        run.status = InteractionStatus.RUNNING
        run.seed_filenames = seed_filenames or None

        # Save initial state
        self._storage.save_run(run)

        # Create interaction with context
        if on_status:
            on_status("Starting revision...")
        interaction_id = self._client.create_interaction_with_context(
            revision_prompt,
            previous_run.interaction_id,
            on_debug=on_debug,
        )
        run.interaction_id = interaction_id

        # Save with interaction_id so we can resume if interrupted
        self._storage.save_run(run)

        # Poll until complete
        return self._poll_and_finalize(run, on_status)

    def resume_incomplete(
        self,
        run_id: str,
        *,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> ResearchRun:
        """
        Resume polling for an incomplete run.

        Use this when a run was interrupted (e.g., by Ctrl+C) after the
        interaction was created but before it completed.
        """
        run = self._storage.load_latest_run(run_id)
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        if run.status == InteractionStatus.COMPLETED:
            raise ValueError("Run already completed")

        if not run.interaction_id:
            raise ValueError("Run has no interaction_id to resume")

        run.status = InteractionStatus.RUNNING
        return self._poll_and_finalize(run, on_status)

    def _poll_and_finalize(
        self,
        run: ResearchRun,
        on_status: Optional[Callable[[str], None]],
    ) -> ResearchRun:
        """Poll until completion and finalize the run."""
        if on_status:
            on_status("Polling for results...")

        poll_result = self._client.poll_interaction(
            run.interaction_id,
            on_status=on_status,
        )

        if poll_result.status == InteractionStatus.COMPLETED:
            run.report_markdown = self._process_citations(
                poll_result.final_markdown, run.run_id, run.version, on_status
            )
            run.status = InteractionStatus.COMPLETED
            run.usage = poll_result.usage
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
        seeds: Optional[list[SeedFile]] = None,
    ) -> str:
        """Build the initial research prompt from template."""
        if questions:
            questions_text = "\n".join(f"- {q}" for q in questions)
        else:
            questions_text = f"- What is {topic}?"

        constraints_text = self._format_constraints(constraints)
        seeds_block = self._format_seeds(seeds or [])

        return INITIAL_RESEARCH_TEMPLATE.format(
            topic=topic,
            questions=questions_text,
            constraints=constraints_text,
            seeds=seeds_block,
        )

    def _format_seeds(self, seeds: list[SeedFile]) -> str:
        """
        Format seed files as a prompt section.

        Returns an empty string when no seeds are provided. Otherwise returns
        a leading blank line plus a `## Seed Documents` section in which each
        seed appears under `### {filename}` inside a fenced code block.
        """
        if not seeds:
            return ""

        blocks: list[str] = [
            "",
            "## Seed Documents",
            "",
            "The user has provided the following source documents for reference. "
            "Treat them as primary starting context, but still perform fresh "
            "research to expand, verify, and cite independently.",
            "",
        ]
        for seed in seeds:
            ext = Path(seed.filename).suffix.lower()
            fence_lang = EXT_TO_FENCE.get(ext, "")
            blocks.append(f"### {seed.filename}")
            blocks.append("")
            blocks.append(f"```{fence_lang}")
            blocks.append(seed.content.rstrip("\n"))
            blocks.append("```")
            blocks.append("")
        return "\n".join(blocks)

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
