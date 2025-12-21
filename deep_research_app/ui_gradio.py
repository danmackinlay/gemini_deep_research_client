"""Gradio web UI for the Deep Research client."""

from pathlib import Path
from tempfile import gettempdir
from typing import Generator

import gradio as gr

from deep_research_app.workflow import ResearchWorkflow, ResearchConstraints
from deep_research_app.storage import RunStorage
from deep_research_app.models import InteractionStatus, UsageMetadata

# Deep Research uses Gemini 3 Pro internally
# See: https://ai.google.dev/gemini-api/docs/deep-research
# Pricing per 1M tokens (as of Dec 2024)
PRICE_PER_M_INPUT = 2.0  # $2 per 1M input tokens
PRICE_PER_M_OUTPUT = 12.0  # $12 per 1M output tokens
# FIXME: Verify current rates and update if changed


def calculate_cost(usage: UsageMetadata | None) -> str:
    """Calculate cost in dollars from token usage and format for display."""
    if not usage:
        return ""
    input_cost = (usage.prompt_tokens / 1_000_000) * PRICE_PER_M_INPUT
    output_cost = (usage.output_tokens / 1_000_000) * PRICE_PER_M_OUTPUT
    total_cost = input_cost + output_cost
    return (
        f"{usage.prompt_tokens:,} in / {usage.output_tokens:,} out | ${total_cost:.4f}"
    )


def prepare_download(report_text: str | None, run_id: str) -> str | None:
    """Write report to temp file and return path for download."""
    if not report_text:
        return None
    path = Path(gettempdir()) / f"{run_id}_report.md"
    path.write_text(report_text, encoding="utf-8")
    return str(path)


def parse_prompt(prompt_text: str) -> dict:
    """Extract structured fields from stored prompt text.

    The prompt follows the INITIAL_RESEARCH_TEMPLATE format with sections like:
    ## Research Topic
    ## Research Questions
    ## Constraints
    """
    result = {
        "topic": "",
        "timeframe": None,
        "region": None,
        "depth": "comprehensive",
        "max_words": None,
        "focus_areas": None,
    }

    # Extract topic between "## Research Topic" and "## Research Questions"
    import re

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

        depth_match = re.search(r"Depth:\s*(.+)", constraints_text)
        if depth_match:
            result["depth"] = depth_match.group(1).strip()

        max_words_match = re.search(r"Maximum length:\s*(\d+)", constraints_text)
        if max_words_match:
            result["max_words"] = int(max_words_match.group(1))

        focus_match = re.search(r"Focus areas:\s*(.+)", constraints_text)
        if focus_match:
            result["focus_areas"] = focus_match.group(1).strip()

    return result


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

            with gr.Row():
                depth_dropdown = gr.Dropdown(
                    choices=["brief", "moderate", "comprehensive"],
                    value="comprehensive",
                    label="Research Depth",
                )
                max_words_input = gr.Number(
                    label="Max Words (optional)",
                    value=None,
                )

            focus_input = gr.Textbox(
                label="Focus Areas (comma-separated)",
                placeholder="e.g., economics, policy, technology",
            )

            # Current report (shown in revision mode)
            current_report_display = gr.Markdown(
                label="Current Report", buttons=["copy"], visible=False
            )

            # Feedback (only shown in revision mode)
            feedback_input = gr.Textbox(
                label="Revision Feedback",
                placeholder="Describe what changes you want...",
                lines=3,
                visible=False,
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

            # Report output
            report_output = gr.Markdown(label="Research Report", buttons=["copy"])
            download_btn = gr.DownloadButton(
                "Download Report (.md)", visible=False, variant="secondary"
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

            # Parse the original prompt to extract fields
            parsed = parse_prompt(run.prompt_text)

            return {
                mode_state: "REVISION",
                loaded_run_id_state: run_id.strip(),
                mode_indicator: f"Mode: Revising run {run_id.strip()} (v{run.version})",
                topic_input: parsed["topic"],
                timeframe_input: parsed["timeframe"] or "",
                region_input: parsed["region"] or "",
                depth_dropdown: parsed["depth"],
                max_words_input: parsed["max_words"],
                focus_input: parsed["focus_areas"] or "",
                current_report_display: gr.Markdown(
                    value=run.report_markdown or "No report available", visible=True
                ),
                feedback_input: gr.Textbox(visible=True),
                action_btn: gr.Button(value="Revise Report"),
                report_output: "",
                status_output: "",
                cost_output: "",
                download_btn: gr.DownloadButton(visible=False),
            }

        def reset_to_new() -> dict:
            """Reset the UI to new research mode."""
            return {
                mode_state: "NEW",
                loaded_run_id_state: "",
                mode_indicator: "Mode: New Research",
                topic_input: "",
                timeframe_input: "",
                region_input: "",
                depth_dropdown: "comprehensive",
                max_words_input: None,
                focus_input: "",
                current_report_display: gr.Markdown(visible=False),
                feedback_input: gr.Textbox(visible=False),
                action_btn: gr.Button(value="Start Research"),
                run_id_input: "",
                report_output: "",
                status_output: "",
                run_id_output: "",
                cost_output: "",
                download_btn: gr.DownloadButton(visible=False),
            }

        def do_research(
            mode: str,
            loaded_run_id: str,
            topic: str,
            timeframe: str,
            region: str,
            depth: str,
            max_words: float | None,
            focus: str,
            feedback: str,
        ) -> Generator:
            """Run new research or revision based on mode."""
            if not topic.strip():
                yield {
                    status_output: "Error: Please enter a topic",
                    run_id_output: "",
                    report_output: "",
                    cost_output: "",
                    download_btn: gr.DownloadButton(visible=False),
                }
                return

            workflow = ResearchWorkflow()

            constraints = ResearchConstraints(
                timeframe=timeframe.strip() or None,
                region=region.strip() or None,
                depth=depth,
                max_words=int(max_words) if max_words else None,
                focus_areas=[a.strip() for a in focus.split(",") if a.strip()] or None,
            )

            accumulated_text = ""
            current_run_id = ""

            def on_event(event_type: str, text: str) -> None:
                nonlocal accumulated_text, current_run_id
                if event_type == "start" and "Interaction started:" in text:
                    current_run_id = text.split(": ")[1]
                elif event_type == "text":
                    accumulated_text += text

            def on_status(status: str) -> None:
                pass  # Could update status in real-time if needed

            try:
                if mode == "REVISION" and loaded_run_id:
                    # Revision mode - need feedback
                    if not feedback.strip():
                        yield {
                            status_output: "Error: Please enter revision feedback",
                            run_id_output: "",
                            report_output: "",
                            cost_output: "",
                            download_btn: gr.DownloadButton(visible=False),
                        }
                        return

                    run = workflow.revise_research(
                        run_id=loaded_run_id,
                        feedback=feedback.strip(),
                        constraints=constraints,
                        on_event=on_event,
                    )
                else:
                    # New research mode
                    run = workflow.run_initial_research(
                        topic=topic,
                        constraints=constraints,
                        on_event=on_event,
                        on_status=on_status,
                    )

                report_text = (
                    run.report_markdown or accumulated_text or "No report generated"
                )
                download_path = prepare_download(
                    run.report_markdown, f"{run.run_id}_v{run.version}"
                )
                yield {
                    status_output: run.status.value,
                    run_id_output: run.run_id,
                    report_output: report_text,
                    cost_output: calculate_cost(run.usage),
                    download_btn: gr.DownloadButton(
                        value=download_path, visible=download_path is not None
                    ),
                }

            except Exception as e:
                yield {
                    status_output: f"Error: {e}",
                    run_id_output: current_run_id,
                    report_output: accumulated_text or "",
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
                depth_dropdown,
                max_words_input,
                focus_input,
                current_report_display,
                feedback_input,
                action_btn,
                report_output,
                status_output,
                cost_output,
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
                depth_dropdown,
                max_words_input,
                focus_input,
                current_report_display,
                feedback_input,
                action_btn,
                run_id_input,
                report_output,
                status_output,
                run_id_output,
                cost_output,
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
                depth_dropdown,
                max_words_input,
                focus_input,
                feedback_input,
            ],
            outputs=[
                status_output,
                run_id_output,
                report_output,
                cost_output,
                download_btn,
            ],
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
                depth_dropdown,
                max_words_input,
                focus_input,
                current_report_display,
                feedback_input,
                action_btn,
                report_output,
                status_output,
                cost_output,
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
