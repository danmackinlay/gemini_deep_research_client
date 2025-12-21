# Gemini Deep Research Client

A local CLI and web client for Google's Gemini Deep Research API. Conduct AI-powered research with streaming output, iterative revisions, and persistent versioned reports.

## See also

* [Gemini Deep Research Agent  |  Gemini API  |  Google AI for Developers](https://ai.google.dev/gemini-api/docs/deep-research)
* [Build with Gemini Deep Research](https://blog.google/technology/developers/deep-research-agent-gemini-api/)

## Setup

This project uses [uv](https://github.com/astral-sh/uv) for dependency management.

### Installation

1. Install uv (if not already installed):
```bash
pip install uv
```

2. Install dependencies:
```bash
uv sync
```

3. Set up your Gemini API key via [direnv](https://direnv.net/):
```bash
# Create .envrc with your API key
echo 'export GEMINI_API_KEY="your-api-key-here"' > .envrc
direnv allow
```

## Usage

### CLI

```bash
# Start a new research run
uv run deep-research new "Impact of AI on healthcare" --timeframe "2020-2024"

# List all research runs
uv run deep-research list

# Show a report
uv run deep-research show <RUN_ID>

# Revise a report with feedback
uv run deep-research revise <RUN_ID> --feedback "Add more focus on economic impacts"

# Check status of a running interaction
uv run deep-research status <INTERACTION_ID>

# Resume an interrupted research run
uv run deep-research resume <RUN_ID>
```

#### CLI Options

```bash
uv run deep-research new "topic" \
  --timeframe "2020-2024" \    # Time period constraint
  --region "United States" \   # Geographic focus
  --max-words 5000 \           # Maximum report length
  --focus "economics,policy"   # Comma-separated focus areas

# Show thinking summaries during streaming
uv run deep-research --show-thoughts new "topic"
```

### Web UI

```bash
uv run python -m deep_research_app.ui_gradio
```

## Features

- **Streaming output**: Watch research progress in real-time
- **Automatic fallback**: Falls back to polling if streaming fails
- **Interrupt & resume**: Ctrl+C saves state; resume later with `deep-research resume`
- **Versioned reports**: Each revision creates a new version, preserving history
- **Iterative revisions**: Refine reports with feedback using previous context

## Output

Reports are saved to `runs/{run_id}/`:
- `prompt_v1.md`, `report_v1.md` - Initial research
- `prompt_v2.md`, `report_v2.md` - After revision
- `meta.json` - Run metadata
