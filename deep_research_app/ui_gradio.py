"""Gradio web UI for the Deep Research client."""

import re
import shutil
import tempfile
from pathlib import Path
from typing import Generator

import gradio as gr

from deep_research_app.workflow import ResearchWorkflow
from deep_research_app.storage import RunStorage
from deep_research_app.models import InteractionStatus, ResearchConstraints


def _slugify(text: str, max_len: int = 60) -> str:
    """Lower-case, underscore-separated slug suitable for a filename stem."""
    s = (text or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:max_len].rstrip("_") or "report"


def _extract_title(markdown: str | None) -> str | None:
    """Return the first `# Heading` line of the markdown (without the `# `)."""
    if not markdown:
        return None
    for line in markdown.splitlines():
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            return m.group(1).strip()
    return None


def _download_alias(report_path: Path, stem: str, version: int) -> str:
    """
    Return a path whose *basename* is `{stem}_v{N}.md`, suitable for
    `gr.DownloadButton(value=...)`. Copies the report to a fresh temp dir;
    Gradio's file cache handles cleanup.
    """
    alias_name = f"{stem or 'report'}_v{version}.md"
    tmp_dir = Path(tempfile.mkdtemp(prefix="deep-research-dl-"))
    alias_path = tmp_dir / alias_name
    shutil.copy2(report_path, alias_path)
    return str(alias_path)


def _resolve_filename_stem(
    storage: RunStorage,
    run_id: str,
    *,
    report_markdown: str | None,
    topic: str | None,
) -> str:
    """
    Return the stable filename stem for `run_id`, persisting the result.

    Resolution: persisted `meta.filename_stem` → first `# H1` of the report
    → slugified topic → `"report"`. Whatever is chosen is written back to
    `meta.json` so subsequent loads are stable.
    """
    meta = storage.load_metadata(run_id)
    if meta and meta.filename_stem:
        return meta.filename_stem
    title = _extract_title(report_markdown)
    if title:
        stem = _slugify(title)
    elif topic and topic.strip():
        stem = _slugify(topic)
    else:
        stem = "report"
    storage.set_filename_stem(run_id, stem)
    return stem


def create_ui() -> gr.Blocks:
    """Create the Gradio web interface."""
    storage = RunStorage()

    with gr.Blocks(title="Gemini Deep Research") as demo:
        gr.Markdown("# Gemini Deep Research Client")

        # State for tracking mode and loaded run
        mode_state = gr.State("NEW")  # "NEW" or "REVISION"
        loaded_run_id_state = gr.State("")

        with gr.Tab("Research"):
            # Mode controls at top
            with gr.Row():
                run_id_input = gr.Textbox(
                    label="Run ID",
                    placeholder="Enter run ID to load for revision...",
                    scale=2,
                )
                load_btn = gr.Button("Load Run", scale=1)
                new_btn = gr.Button("New Research", variant="secondary", scale=1)

            # Mode indicator
            mode_indicator = gr.Textbox(
                value="Mode: New Research",
                label="",
                interactive=False,
                container=False,
            )

            # Research parameters
            topic_input = gr.Textbox(
                label="Research Topic",
                placeholder="Enter your research topic or question...",
                lines=3,
            )

            with gr.Row():
                timeframe_input = gr.Textbox(
                    label="Timeframe",
                    placeholder="e.g., 2020-2024",
                )
                region_input = gr.Textbox(
                    label="Region",
                    placeholder="e.g., United States",
                )

            max_words_input = gr.Number(
                label="Max Words (optional)",
                value=None,
            )

            focus_input = gr.Textbox(
                label="Focus Areas (comma-separated)",
                placeholder="e.g., economics, policy, technology",
            )

            seeds_input = gr.File(
                label="Seed Documents (optional)",
                file_count="multiple",
            )

            # Feedback (only shown in revision mode)
            feedback_input = gr.Textbox(
                label="Revision Feedback",
                placeholder="Describe what changes you want...",
                lines=3,
                visible=False,
            )

            # Download filename — auto-derived from the report's title once
            # research completes; persisted across sessions in meta.json.
            filename_input = gr.Textbox(
                label="Download filename (optional)",
                placeholder="auto-generated from report title; edit to override",
                info="Becomes the .md filename when you click Download.",
            )

            # Action button - label changes based on mode
            action_btn = gr.Button("Start Research", variant="primary")

            # Status outputs
            with gr.Row():
                status_output = gr.Textbox(
                    label="Status",
                    interactive=False,
                )
                run_id_output = gr.Textbox(
                    label="Run ID",
                    interactive=False,
                )
                cost_output = gr.Textbox(
                    label="Usage & Cost",
                    interactive=False,
                )

            # Download sits above the rendered report so it's visible without
            # scrolling through the whole document.
            download_btn = gr.DownloadButton(
                "Download Report (.md)", visible=False, variant="secondary"
            )

            # Rendered report
            report_output = gr.Markdown(label="Research Report", buttons=["copy"])

            # Raw markdown source — collapsed by default; copy button copies
            # the unrendered text.
            with gr.Accordion("View / copy markdown source", open=False):
                report_source = gr.Code(
                    label="Markdown source",
                    language="markdown",
                    lines=15,
                    max_lines=40,
                    interactive=False,
                    show_label=False,
                    wrap_lines=True,
                    show_line_numbers=False,
                )

        with gr.Tab("History"):
            gr.Markdown("Click a row to select, then click 'Load Selected Run'")
            refresh_btn = gr.Button("Refresh")
            runs_table = gr.Dataframe(
                headers=["Run ID", "Topic", "Version", "Created"],
                interactive=False,
            )
            selected_run_id_state = gr.State("")
            selected_run_display = gr.Textbox(
                label="Selected Run",
                interactive=False,
                value="No run selected",
            )
            load_selected_btn = gr.Button("Load Selected Run", variant="primary")

        # Event handlers
        def load_run(run_id: str) -> dict:
            """Load a run for revision, populating all fields."""
            if not run_id.strip():
                return {
                    mode_indicator: "Error: Please enter a Run ID",
                    mode_state: "NEW",
                }

            run = storage.load_latest_run(run_id.strip())
            if not run:
                return {
                    mode_indicator: f"Error: Run not found: {run_id}",
                    mode_state: "NEW",
                }

            if run.status != InteractionStatus.COMPLETED:
                return {
                    mode_indicator: f"Error: Cannot revise - run status is {run.status}",
                    mode_state: "NEW",
                }

            # Use stored inputs (preserved across revisions)
            inputs = run.inputs
            topic = inputs.topic if inputs else ""
            constraints = inputs.constraints if inputs else None

            return {
                mode_state: "REVISION",
                loaded_run_id_state: run_id.strip(),
                mode_indicator: f"Mode: Revising run {run_id.strip()} (v{run.version})",
                topic_input: gr.Textbox(value=topic, interactive=False),
                timeframe_input: (constraints.timeframe or "") if constraints else "",
                region_input: (constraints.region or "") if constraints else "",
                max_words_input: constraints.max_words if constraints else None,
                focus_input: (
                    ", ".join(constraints.focus_areas)
                    if constraints and constraints.focus_areas
                    else ""
                ),
                seeds_input: None,
                feedback_input: gr.Textbox(visible=True),
                action_btn: gr.Button(value="Revise Report"),
                report_output: run.report_markdown or "No report available",
                report_source: run.report_markdown or "",
                status_output: "",
                cost_output: "",
                filename_input: _resolve_filename_stem(
                    storage,
                    run.run_id,
                    report_markdown=run.report_markdown,
                    topic=topic,
                ),
                download_btn: (
                    gr.DownloadButton(
                        value=_download_alias(
                            storage.get_report_path(run.run_id, run.version),
                            _resolve_filename_stem(
                                storage,
                                run.run_id,
                                report_markdown=run.report_markdown,
                                topic=topic,
                            ),
                            run.version,
                        ),
                        visible=True,
                    )
                    if storage.get_report_path(run.run_id, run.version)
                    else gr.DownloadButton(visible=False)
                ),
            }

        def reset_to_new() -> dict:
            """Reset the UI to new research mode."""
            return {
                mode_state: "NEW",
                loaded_run_id_state: "",
                mode_indicator: "Mode: New Research",
                topic_input: gr.Textbox(value="", interactive=True),
                timeframe_input: "",
                region_input: "",
                max_words_input: None,
                focus_input: "",
                seeds_input: None,
                feedback_input: gr.Textbox(visible=False),
                action_btn: gr.Button(value="Start Research"),
                run_id_input: "",
                report_output: "",
                report_source: "",
                status_output: "",
                run_id_output: "",
                cost_output: "",
                filename_input: "",
                download_btn: gr.DownloadButton(visible=False),
            }

        def do_research(
            mode: str,
            loaded_run_id: str,
            topic: str,
            timeframe: str,
            region: str,
            max_words: float | None,
            focus: str,
            feedback: str,
            seeds: list | None,
        ) -> Generator:
            """Run new research or revision based on mode."""
            if mode == "REVISION" and loaded_run_id:
                if not feedback.strip():
                    yield {
                        status_output: "Error: Please enter revision feedback",
                        run_id_output: "",
                        report_output: "",
                        report_source: "",
                        cost_output: "",
                        download_btn: gr.DownloadButton(visible=False),
                    }
                    return
            else:
                if not topic.strip():
                    yield {
                        status_output: "Error: Please enter a topic",
                        run_id_output: "",
                        report_output: "",
                        report_source: "",
                        cost_output: "",
                        download_btn: gr.DownloadButton(visible=False),
                    }
                    return

            workflow = ResearchWorkflow()

            constraints = ResearchConstraints.from_user_input(
                timeframe=timeframe,
                region=region,
                max_words=max_words,
                focus=focus,
            )

            # Gradio returns None when nothing uploaded; coerce to an empty list.
            # Each upload object exposes a .name attribute with the temp path.
            seed_paths: list[Path] = []
            for f in seeds or []:
                path = getattr(f, "name", f)
                seed_paths.append(Path(path))

            # Show "in progress" while polling
            yield {
                status_output: "Running... (this may take several minutes)",
                run_id_output: "",
                report_output: "",
                report_source: "",
                cost_output: "",
                download_btn: gr.DownloadButton(visible=False),
            }

            try:
                if mode == "REVISION" and loaded_run_id:
                    run = workflow.revise_research(
                        run_id=loaded_run_id,
                        feedback=feedback.strip(),
                        constraints=constraints,
                        seed_paths=seed_paths or None,
                    )
                else:
                    run = workflow.run_initial_research(
                        topic=topic,
                        constraints=constraints,
                        seed_paths=seed_paths or None,
                    )

                report_text = run.report_markdown or "No report generated"
                download_path = storage.get_report_path(run.run_id, run.version)
                effective_topic = run.inputs.topic if run.inputs else topic
                stem = _resolve_filename_stem(
                    storage,
                    run.run_id,
                    report_markdown=run.report_markdown,
                    topic=effective_topic,
                )

                # Auto-transition into REVISION mode for the just-finished run.
                yield {
                    status_output: run.status.value,
                    run_id_output: run.run_id,
                    report_output: report_text,
                    report_source: report_text,
                    cost_output: run.usage.format_cost(include_total=False)
                    if run.usage
                    else "",
                    filename_input: stem,
                    download_btn: (
                        gr.DownloadButton(
                            value=_download_alias(download_path, stem, run.version),
                            visible=True,
                        )
                        if download_path
                        else gr.DownloadButton(visible=False)
                    ),
                    mode_state: "REVISION",
                    loaded_run_id_state: run.run_id,
                    mode_indicator: f"Mode: Revising run {run.run_id} (v{run.version})",
                    feedback_input: gr.Textbox(value="", visible=True),
                    action_btn: gr.Button(value="Revise Report"),
                    topic_input: gr.Textbox(value=effective_topic, interactive=False),
                }

            except Exception as e:
                yield {
                    status_output: f"Error: {e}",
                    run_id_output: "",
                    report_output: "",
                    report_source: "",
                    cost_output: "",
                    download_btn: gr.DownloadButton(visible=False),
                }

        def refresh_runs() -> list[list]:
            """Refresh the runs table."""
            runs = storage.list_runs()
            return [
                [
                    r.run_id,
                    r.topic[:50] + "..." if len(r.topic) > 50 else r.topic,
                    r.latest_version,
                    r.created_at,
                ]
                for r in runs
            ]

        def on_row_select(evt: gr.SelectData) -> dict:
            """Handle row selection in history table."""
            if evt.index is not None and evt.value:
                # evt.index is [row, col], we want row 0 column (Run ID)
                # But evt.value is the selected cell value
                # We need to get the Run ID from the row
                run_id = str(evt.row_value[0]) if evt.row_value else ""
                return {
                    selected_run_id_state: run_id,
                    selected_run_display: f"Selected: {run_id}",
                }
            return {
                selected_run_id_state: "",
                selected_run_display: "No run selected",
            }

        def load_selected_run(selected_run_id: str) -> dict:
            """Load the selected run from history."""
            if not selected_run_id:
                return {
                    mode_indicator: "Error: No run selected",
                    mode_state: "NEW",
                }
            return load_run(selected_run_id)

        def on_filename_change(filename: str, run_id: str):
            """Persist the new stem and refresh the download alias."""
            if not run_id:
                return gr.update()
            new_stem = _slugify(filename) if filename.strip() else None
            storage.set_filename_stem(run_id, new_stem)
            meta = storage.load_metadata(run_id)
            if not meta:
                return gr.DownloadButton(visible=False)
            version = meta.latest_version
            report_path = storage.get_report_path(run_id, version)
            if not report_path:
                return gr.DownloadButton(visible=False)
            return gr.DownloadButton(
                value=_download_alias(report_path, new_stem or "report", version),
                visible=True,
            )

        # Wire up events
        load_btn.click(
            fn=load_run,
            inputs=[run_id_input],
            outputs=[
                mode_state,
                loaded_run_id_state,
                mode_indicator,
                topic_input,
                timeframe_input,
                region_input,
                max_words_input,
                focus_input,
                seeds_input,
                feedback_input,
                action_btn,
                report_output,
                report_source,
                status_output,
                cost_output,
                filename_input,
                download_btn,
            ],
        )

        new_btn.click(
            fn=reset_to_new,
            outputs=[
                mode_state,
                loaded_run_id_state,
                mode_indicator,
                topic_input,
                timeframe_input,
                region_input,
                max_words_input,
                focus_input,
                seeds_input,
                feedback_input,
                action_btn,
                run_id_input,
                report_output,
                report_source,
                status_output,
                run_id_output,
                cost_output,
                filename_input,
                download_btn,
            ],
        )

        action_btn.click(
            fn=do_research,
            inputs=[
                mode_state,
                loaded_run_id_state,
                topic_input,
                timeframe_input,
                region_input,
                max_words_input,
                focus_input,
                feedback_input,
                seeds_input,
            ],
            outputs=[
                status_output,
                run_id_output,
                report_output,
                report_source,
                cost_output,
                filename_input,
                download_btn,
                mode_state,
                loaded_run_id_state,
                mode_indicator,
                feedback_input,
                action_btn,
                topic_input,
            ],
        )

        filename_input.change(
            fn=on_filename_change,
            inputs=[filename_input, loaded_run_id_state],
            outputs=[download_btn],
        )

        refresh_btn.click(
            fn=refresh_runs,
            outputs=[runs_table],
        )

        runs_table.select(
            fn=on_row_select,
            outputs=[selected_run_id_state, selected_run_display],
        )

        load_selected_btn.click(
            fn=load_selected_run,
            inputs=[selected_run_id_state],
            outputs=[
                mode_state,
                loaded_run_id_state,
                mode_indicator,
                topic_input,
                timeframe_input,
                region_input,
                max_words_input,
                focus_input,
                seeds_input,
                feedback_input,
                action_btn,
                report_output,
                report_source,
                status_output,
                cost_output,
                filename_input,
                download_btn,
            ],
        )

    return demo


def launch() -> None:
    """Launch the Gradio interface."""
    demo = create_ui()
    demo.launch()


if __name__ == "__main__":
    launch()
