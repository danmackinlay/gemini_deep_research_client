# gemini_deep_research_client

A local client for Google Gemini deep research built with Gradio and Google Generative AI.

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

### Running the Application

```bash
uv run python main.py
```

## Dependencies

- **gradio** (>=6.1.0): Web UI framework for building interactive interfaces
- **google-genai** (>=1.55.0): Google Generative AI SDK for accessing Gemini models

## Development

Python version: 3.12+