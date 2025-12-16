# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run CLI
uv run deep-research --help
uv run deep-research new "Research topic" --timeframe "2020-2024" --depth comprehensive
uv run deep-research list
uv run deep-research show <RUN_ID>
uv run deep-research revise <RUN_ID> --feedback "Add more detail on X"

# Launch Gradio web UI
uv run python -m deep_research_app.ui_gradio

# Environment setup (API key via direnv)
direnv allow
```

## Architecture

This is a client for Google's Gemini Deep Research API (agent: `deep-research-pro-preview-12-2025`). The architecture has three layers:

### Layer 1: API Client (`deep_research.py`)
`DeepResearchClient` wraps the `google-genai` SDK's Interactions API:
- `start_research()` - Creates interaction with streaming, handles `interaction.start`, `content.delta`, `interaction.complete` events
- `start_research_with_context()` - Same but includes `previous_interaction_id` for revisions
- `resume_stream()` - Resumes interrupted streams using `last_event_id`
- `poll_interaction()` - Fallback polling when streaming fails

All methods return dataclasses (`StartResult`, `ResumeResult`, `PollResult`) and accept a `StreamCallback` for real-time event handling.

### Layer 2: Workflow Orchestration (`workflow.py`)
`ResearchWorkflow` provides high-level operations:
- `run_initial_research()` - Streams research, auto-falls back to polling, persists state for resume
- `revise_research()` - Creates versioned revision using previous interaction context
- `resume_interrupted()` - Continues from saved stream state

Prompt templates (`INITIAL_RESEARCH_TEMPLATE`, `REVISION_TEMPLATE`) define the output format (Executive Summary, Key Findings, Evidence, etc.).

### Layer 3: Persistence (`storage.py`)
`RunStorage` manages `runs/{run_id}/` directories:
- `prompt_v{N}.md`, `report_v{N}.md` - Versioned prompts and reports
- `meta.json` - Run metadata (interaction IDs, timestamps, status)
- `stream_state.json` - Temporary state for resume capability (deleted on completion)

### Data Flow
1. CLI/UI calls `ResearchWorkflow` methods
2. Workflow wraps callbacks to save stream state, calls `DeepResearchClient`
3. Client streams from Gemini API, accumulates text, returns result
4. Workflow saves final report via `RunStorage`

## Key Patterns

- **Streaming with polling fallback**: All research operations try streaming first, fall back to polling if incomplete
- **Interrupt handling**: Ctrl+C saves stream state; use `resume` command to continue
- **Versioned runs**: Each revision increments version, preserves previous reports
- **Callback-based streaming**: `StreamCallback = Callable[[str, str], None]` receives `(event_type, text)` tuples
- **Usage tracking**: Token counts captured from `chunk.interaction.usage` on `interaction.complete` events. Fields: `total_input_tokens`, `total_output_tokens`, `total_tokens`, `total_reasoning_tokens`. Persisted in `meta.json` per version, displayed in CLI output.

## Configuration

Settings loaded via `pydantic-settings` from environment:
- `GEMINI_API_KEY` (required) - Set in `.envrc` for direnv
- `runs_dir` - Default `./runs`
- `agent_name` - Default `deep-research-pro-preview-12-2025`

## Engineering policies
- No backward-compat promises—break APIs when it improves the stack, then update configs/docs. Delete rather than deprecate.
- Prefer in-code asserts over permissive behaviour; fail early on shape/layout issues.
- always lint code with `uv run ruff format; uv run ruff check --fix` but it is advisory; we only block on syntax/import errors.
- Leave `# FIXME` notes (with reasons) instead of half-built abstractions.