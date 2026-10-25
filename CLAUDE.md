# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run CLI
uv run deep-research --help
uv run deep-research new "Research topic" --timeframe "2020-2024"
uv run deep-research list
uv run deep-research show <RUN_ID>
uv run deep-research revise <RUN_ID> --feedback "Add more detail on X"

# Launch Gradio web UI
uv run python -m deep_research_app.ui_gradio

# Environment setup (API key via direnv)
direnv allow
```

## Architecture

This is a client for Google's Gemini Deep Research agent via the Interactions API (default agent `deep-research-preview-04-2026`; `--max` selects `deep-research-max-preview-04-2026`). Agent IDs are preview releases with announced shutdown dates; check https://ai.google.dev/gemini-api/docs/deprecations. The architecture has three layers:

### Layer 1: API Client (`deep_research.py`)
`DeepResearchClient` wraps the `google-genai` (>=2.x) SDK's Interactions API. It is poll-only: streaming was removed because the connection times out long before research finishes (see module docstring).
- `create_interaction()` - Starts a `background=True` interaction, returns its ID
- `create_interaction_with_context()` - Same but with `previous_interaction_id` for revisions
- `poll_interaction()` - Polls until a terminal status, returns `PollResult`
- `get_interaction_status()` - Single poll

The report is the content of the last `model_output` entry in `interaction.steps` (the API's legacy `outputs` array was removed June 2026). Agent-generated images become `![Figure N](report_v{V}_fig{N}.ext)` links and are returned as `ReportImage` bytes for storage to save. API statuses are mapped through `_STATUS_MAP`; unknown statuses assert.

### Layer 2: Workflow Orchestration (`workflow.py`)
`ResearchWorkflow` provides high-level operations:
- `run_initial_research()` - Creates an interaction, persists the run, polls to completion
- `revise_research()` - Creates versioned revision using previous interaction context
- `resume_incomplete()` - Resumes polling a run whose interaction ID was saved

Prompt templates (`INITIAL_RESEARCH_TEMPLATE`, `REVISION_TEMPLATE`) define the output format (Executive Summary, Key Findings, Evidence, etc.).

### Layer 3: Persistence (`storage.py`)
`RunStorage` manages `runs/{run_id}/` directories:
- `prompt_v{N}.md`, `report_v{N}.md` - Versioned prompts and reports
- `meta.json` - Run metadata (interaction IDs, timestamps, status, usage)
- `report_v{N}_fig{M}.*` - Agent-generated figures, linked relatively from the report
- Per-version sources and seed documents

### Data Flow
1. CLI/UI calls `ResearchWorkflow` methods
2. Workflow creates the interaction and saves the run (so it can be resumed), then polls via `DeepResearchClient`
3. Workflow post-processes citations and saves the final report via `RunStorage`

## Key Patterns

- **Background + polling**: Research takes minutes (agent max is 60); the interaction ID is saved before polling so Ctrl+C or timeouts can be recovered with `resume`
- **Versioned runs**: Each revision increments version, preserves previous reports
- **Usage tracking**: `_extract_usage` reads `interaction.usage` on completion (populated since the 04-2026 agents): input, cached, output, thought and tool-use tokens plus Google Search query count. `total_tokens` = input + output + thought + tool_use; cached is a subset of input. `UsageMetadata.calculate_cost` prices these at Gemini 3.1 Pro list rates (`PRICE_PER_*` in `models.py`); search fees are an upper bound since the monthly free tier is ignored.

## Configuration

Settings loaded via `pydantic-settings` from environment (`config.py`):
- `GEMINI_API_KEY` (required) - Set in `.envrc` for direnv
- `runs_dir` - Default `./runs`
- `agent_name` - Default `deep-research-preview-04-2026`
- `thinking_summaries` - `"auto"` or `"none"`
- `visualization` - `"auto"` (default) or `"off"`

### Web UI (`ui_gradio.py`)
- Research runs on a daemon thread; the handler generator yields elapsed-time status every 2s, with buttons disabled until it finishes. `show_progress="hidden"` because Gradio's default highlights every output component for the whole run.
- Figures render via Gradio's file route (`launch(allowed_paths=[runs_dir])`, links rewritten by `_display_markdown`); Download is a `.zip` (report + figures) when a report has figures.

## Engineering policies
- No backward-compat promises—break APIs when it improves the stack, then update configs/docs. Delete rather than deprecate.
- Prefer in-code asserts over permissive behaviour; fail early on data format issues.
- always lint code with `uv run ruff format; uv run ruff check --fix` but it is advisory; we only block on syntax/import errors.
- Leave `# FIXME` notes (with reasons) instead of half-built abstractions.