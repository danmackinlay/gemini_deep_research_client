A local `uv`‑based Python app is perfectly fine; Deep Research is slow but not especially heavy on your side, since Google runs the agent.  Below is a self‑contained plan you can hand to a coding assistant.[1][2]

## 1. Project structure and tooling

- Use `uv` to manage the environment and dependencies (`google-genai`, `typer` or `click`, and optionally `gradio`).  
- Target a layout like:  
  - `pyproject.toml` (managed by `uv`, includes `google-genai` and CLI/UI deps).  
  - `deep_research_app/`
    - `main.py` (CLI entrypoint).  
    - `config.py` (API key, defaults).  
    - `deep_research.py` (low‑level Gemini interactions).  
    - `workflow.py` (higher‑level research + revision workflow).  
    - `storage.py` (saving Markdown + metadata).  
    - `ui_gradio.py` (optional web UI).  
  - `runs/` (per‑research run directories with reports, logs, metadata).

Configure the app to read the Gemini API key from an env var (for example `GEMINI_API_KEY`) and initialize a single `genai.Client()` for reuse.[3][1]

## 2. Core Deep Research client module

Implement `deep_research.py` around the Interactions API and the Deep Research agent.[1][3]

- Initialize the client:

  - `from google import genai`  
  - `client = genai.Client()` (API key taken from env or config).[1]

- Implement three core functions:

  - `start_research(prompt: str, *, stream: bool = True, thinking_summaries: str = "auto") -> StartResult`  
    - Calls `client.interactions.create` with:
      - `input=prompt`.  
      - `agent="deep-research-pro-preview-12-2025"`.[3][1]
      - `background=True` (required for long‑running tasks).[1]
      - `stream=stream`.  
      - `agent_config={"type": "deep-research", "thinking_summaries": thinking_summaries}`.[1]
    - If `stream=True`, yield streaming chunks to the caller while capturing:
      - `interaction_id` from `chunk.interaction.id` when `chunk.event_type == "interaction.start"`.[1]
      - `last_event_id` from `chunk.event_id`.[1]
      - All `delta.text` segments into a list of Markdown chunks when `chunk.event_type == "content.delta"` and `chunk.delta.type == "text"`.[1]
      - Optional thought summaries when `chunk.delta.type == "thought_summary"` for logging.[1]
    - Return an object containing `interaction_id`, `last_event_id`, `final_markdown` (if streaming reached `interaction.complete`), and a flag `complete_via_stream`.[1]

  - `resume_stream(interaction_id: str, last_event_id: str) -> ResumeResult`  
    - Supports resuming interrupted streams using the documented resume endpoint with query params `after=LAST_EVENT_ID`.[1]
    - Same event processing as `start_research`, but it does not create a new interaction; it continues the previous one until `interaction.complete` or error.[1]

  - `poll_interaction(interaction_id: str, interval: float = 10.0, timeout: Optional[float] = None) -> PollResult`  
    - Periodically calls `client.interactions.get(interaction_id)` until:
      - `status == "completed"`: then take `interaction.outputs[-1].text` as the final Markdown.[1]
      - `status == "failed"`: return error info.[1]
      - `timeout` is exceeded: signal timeout.  
    - Used as a fallback when streaming fails or when running in non‑interactive batch mode.

Define data classes (`StartResult`, `ResumeResult`, `PollResult`) for structured return values (interaction ID, text, status).

## 3. Iterative feedback / revision API wrapper

Implement higher‑level functions in `workflow.py` that expose OpenAI‑style “revise this document” semantics using `previous_interaction_id`.[3][1]

- `run_initial_research(topic: str, constraints: dict, format_spec: dict) -> ResearchRun`  
  - Build a prompt template that tells Deep Research to output a **single, self‑contained Markdown document**.[1]
    - Include:
      - Topic and research questions.  
      - Constraints (time period, geography, depth, max words).  
      - Output spec: headings (e.g. Executive Summary, Key Findings, Evidence Table, Limitations), citation style, and Markdown tables.  
      - Explicit instructions: “Do not include commentary about your process; output only the report in Markdown.”  
  - Call `start_research` (streaming).  
  - If streaming completes cleanly, take `final_markdown` from the stream.[1]
  - If streaming fails mid‑way:
    - Use `poll_interaction(interaction_id)` to recover the final output.[1]
  - Return a `ResearchRun` object containing:
    - `run_id` (local UUID).  
    - `interaction_id`.  
    - `version = 1`.  
    - `prompt_text`.  
    - `report_markdown`.  
    - `created_at`.

- `revise_research(previous_run: ResearchRun, user_feedback: str, regenerate_full: bool = True) -> ResearchRun`  
  - Build a revision prompt that:
    - Refers to “the report you produced in the previous interaction.”  
    - Embeds the user feedback.  
    - Asks for a **full revised report** (not a diff) when `regenerate_full=True`.  
  - Call `client.interactions.create` with:
    - `input=revision_prompt`.  
    - `agent="deep-research-pro-preview-12-2025"`.[1]
    - `previous_interaction_id=previous_run.interaction_id`.[1]
    - `background=True`, `stream=True`, same `agent_config`.[1]
  - Handle streaming + fallback polling as in the initial run.[1]
  - Produce a new `ResearchRun`:
    - `run_id` same as initial (logical project).  
    - `interaction_id` of the revision.  
    - `version = previous_run.version + 1`.  
    - Save `feedback` and `report_markdown`.

This gives you an explicit object model for versioned reports and user feedback history.

## 4. Storage and versioning

Implement `storage.py` to persist runs under `runs/`.

- Directory layout:

  - `runs/{run_id}/`
    - `prompt_v1.md`, `report_v1.md`.  
    - `prompt_v2.md`, `report_v2.md`.  
    - `meta.json` (all interaction IDs, timestamps, feedback, status).  
    - `logs/stream_v1.log`, etc.

- Implement functions:

  - `save_run(run: ResearchRun, base_dir: Path) -> None`  
    - Writes the prompt and report for that version.  
    - Appends/updates `meta.json`.  

  - `load_latest_run(run_id: str) -> ResearchRun`  
    - Reads `meta.json` and the highest version’s report.

  - Optionally `list_runs()` to show existing projects.

This storage layer keeps Gemini‑specific IDs separate from local run IDs.

## 5. CLI interface (`main.py`)

Use `typer` or `click` to expose a developer‑friendly CLI.

- Commands:

  - `deep-research new "topic..."`  
    - Creates a new run directory and `run_id`.  
    - Optionally accepts flags: `--timeframe`, `--region`, `--max-words`, `--focus`.  
    - Constructs the prompt and calls `run_initial_research`.  
    - Streams progress to stdout (status lines, thought summaries if enabled).[1]
    - On completion, prints the path to `report_v1.md`.

  - `deep-research revise RUN_ID`  
    - Loads latest `ResearchRun` and prints a brief summary of the last report (e.g. headings).  
    - Prompts the user (stdin) for feedback.  
    - Calls `revise_research` and writes `report_v{N+1}.md`.  

  - `deep-research show RUN_ID`  
    - Prints the latest report to stdout or opens it in `$PAGER`.

  - `deep-research status INTERACTION_ID`  
    - Calls `poll_interaction` once (no loop) and prints status + ETA hint.

- Behavior details:

  - For streaming, print text as it arrives, but still capture full Markdown in memory for saving.[1]
  - Print thought summaries prefixed with something like `[THOUGHT]` if you keep them; make this toggleable via `--show-thoughts`.[1]
  - Handle Ctrl‑C by stopping streaming but keeping the `interaction_id`, then advise: “Run `deep-research status INTERACTION_ID` to check completion and retrieve the final report.”

## 6. Optional Gradio front‑end (`ui_gradio.py`)

Wrap the workflow module with a lightweight local web UI.

- Screens/components:

  - “New research” form:
    - Textbox: topic / question.  
    - Text areas: scope, constraints.  
    - Dropdowns: timeframe presets, region presets, “depth” level.  
  - While running:
    - Text area showing streaming report text.  
    - Optional area for thought summaries.[1]
  - After completion:
    - Button to download `report_vN.md`.  
    - Text area for feedback; “Revise” button that calls `revise_research`.

- Implementation details:

  - Backend calls the same `workflow.py` functions.  
  - For streaming, use Gradio’s generator interfaces to push text updates incrementally.

This UI is purely sugar over the CLI; the core logic remains in your workflow layer.

## 7. Prompt templates for good Markdown reports

Bake reusable templates into `workflow.py` or a `templates.py` module.

- Initial prompt template:

  - Includes:
    - Task: “Act as an expert research analyst using the Gemini Deep Research agent. Plan, search, read, and synthesize multiple sources.”[2][1]
    - Constraints: “Focus on X; include Y; time horizon; avoid speculative claims beyond Z.”  
    - Output format:
      - “Produce a single Markdown document with headings: 1. Executive summary, 2. Key questions, 3. Main findings, 4. Evidence and citations, 5. Limitations and open questions.”  
      - “Use Markdown tables where helpful.”  
      - “Do not include explanations of your internal process.”

- Revision prompt template:

  - “You previously produced a report in the preceding interaction. The user feedback is: ‘...’. Produce a complete revised Markdown report that incorporates this feedback while preserving correct facts and citations.”

Having these templates as constants or Jinja‑style templates makes it easy to iterate.

***

Hand this plan to your coding assistant as “build a `uv`‑managed Python package with these modules, functions, and CLI commands, implementing Deep Research according to the official Interactions API and deep‑research docs.” The heavy lifting is in wiring `interactions.create` (with `background`, `stream`, `agent_config`, and `previous_interaction_id`) into a robust streaming + polling loop and saving each versioned Markdown report to disk.[3][1]

[1](https://ai.google.dev/gemini-api/docs/deep-research)
[2](https://www.unifiedaihub.com/ai-news/google-opens-gemini-deep-research-to-developers-game-changer-for-ai-powered-research-applications)
[3](https://ai.google.dev/gemini-api/docs/interactions)
[4](https://smallbiztrends.com/google-launches-advanced-gemini-deep-research-agent-for-developers/)
[5](https://techcrunch.com/2025/12/11/google-launched-its-deepest-ai-research-agent-yet-on-the-same-day-openai-dropped-gpt-5-2/)
[6](https://developers.googleblog.com/building-agents-with-the-adk-and-the-new-interactions-api/)
[7](https://gemini.google/overview/deep-research/)
[8](https://cloud.google.com/gemini-enterprise/agents)
[9](https://dev.to/proflead/ai-developer-digest-gemini-deep-research-gpt-52-and-agent-tools-231f)
[10](https://ai.google.dev/gemini-api/docs/deep-research?hl=ar)
[11](https://9to5google.com/2025/12/11/gemini-deep-research-agent/)
[12](https://blog.google/technology/developers/interactions-api/)
[13](https://www.youtube.com/watch?v=39mZvpN0k-Q)
[14](https://docs.cloud.google.com/gemini/enterprise/docs/research-assistant)
[15](https://ai.google.dev/api/interactions-api)
[16](https://cloud.google.com/blog/products/ai-machine-learning/build-a-deep-research-agent-with-google-adk)
[17](https://blog.google/technology/developers/deep-research-agent-gemini-api/)
[18](https://www.linkedin.com/posts/philipp-schmid-a6a2bb196_excited-to-introduce-the-gemini-interactions-activity-7404929317971152896-VMpn)
[19](https://github.com/google-gemini/cookbook)
[20](https://blog.google/products/gemini/google-gemini-deep-research/)
