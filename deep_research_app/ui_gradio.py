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


def create_ui() -> gr.Blocks:
    """Create the Gradio web interface."""
    storage = RunStorage()

    with gr.Blocks(title="Gemini Deep Research") as demo:
        gr.Markdown("# Gemini Deep Research Client")

        with gr.Tab("New Research"):
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

            start_btn = gr.Button("Start Research", variant="primary")

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

            report_output = gr.Markdown(label="Research Report", buttons=["copy"])
            download_btn = gr.DownloadButton(
                "Download Report (.md)", visible=False, variant="secondary"
            )

            thoughts_output = gr.Textbox(
                label="Thinking Summaries",
                lines=5,
                interactive=False,
                visible=False,
            )

        with gr.Tab("Revise"):
            revise_run_id = gr.Textbox(label="Run ID to Revise")
            load_btn = gr.Button("Load Run")

            current_report_display = gr.Markdown(
                label="Current Report", buttons=["copy"]
            )

            feedback_input = gr.Textbox(
                label="Feedback",
                placeholder="Describe what changes you want...",
                lines=3,
            )

            revise_btn = gr.Button("Revise Report", variant="primary")
            with gr.Row():
                revised_status = gr.Textbox(label="Revision Status", interactive=False)
                revised_cost_output = gr.Textbox(
                    label="Usage & Cost", interactive=False
                )
            revised_report_output = gr.Markdown(
                label="Revised Report", buttons=["copy"]
            )
            revised_download_btn = gr.DownloadButton(
                "Download Report (.md)", visible=False, variant="secondary"
            )

        with gr.Tab("History"):
            refresh_btn = gr.Button("Refresh")
            runs_table = gr.Dataframe(
                headers=["Run ID", "Topic", "Version", "Created"],
                interactive=False,
            )

        # Event handlers
        def run_research(
            topic: str,
            timeframe: str,
            region: str,
            depth: str,
            max_words: float | None,
            focus: str,
        ) -> Generator:
            """Run research and yield updates."""
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
                focus_areas=focus.strip().split(",") if focus.strip() else None,
            )

            accumulated_text = ""
            thoughts: list[str] = []
            current_run_id = ""
            current_status = "Starting..."

            def on_event(event_type: str, text: str) -> None:
                nonlocal accumulated_text, current_run_id, current_status
                if event_type == "start" and "Interaction started:" in text:
                    current_run_id = text.split(": ")[1]
                    current_status = "Running..."
                elif event_type == "text":
                    accumulated_text += text
                elif event_type == "thought":
                    thoughts.append(text)
                elif event_type == "complete":
                    current_status = "Complete!"

            def on_status(status: str) -> None:
                nonlocal current_status
                current_status = status

            try:
                run = workflow.run_initial_research(
                    topic=topic,
                    constraints=constraints,
                    on_event=on_event,
                    on_status=on_status,
                )

                report_text = (
                    run.report_markdown or accumulated_text or "No report generated"
                )
                download_path = prepare_download(run.report_markdown, run.run_id)
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

        def load_run_for_revision(run_id: str) -> dict:
            """Load a run for revision."""
            if not run_id.strip():
                return {current_report_display: "Please enter a Run ID"}

            run = storage.load_latest_run(run_id.strip())
            if not run:
                return {current_report_display: f"Run not found: {run_id}"}

            if run.status != InteractionStatus.COMPLETED:
                return {
                    current_report_display: f"Cannot revise: run status is {run.status}"
                }

            return {
                current_report_display: run.report_markdown or "No report available"
            }

        def do_revision(run_id: str, feedback: str) -> Generator:
            """Perform a revision."""
            if not run_id.strip():
                yield {
                    revised_status: "Error: Please enter a Run ID",
                    revised_report_output: "",
                    revised_cost_output: "",
                    revised_download_btn: gr.DownloadButton(visible=False),
                }
                return

            if not feedback.strip():
                yield {
                    revised_status: "Error: Please enter feedback",
                    revised_report_output: "",
                    revised_cost_output: "",
                    revised_download_btn: gr.DownloadButton(visible=False),
                }
                return

            workflow = ResearchWorkflow()

            accumulated_text = ""

            def on_event(event_type: str, text: str) -> None:
                nonlocal accumulated_text
                if event_type == "text":
                    accumulated_text += text

            try:
                run = workflow.revise_research(
                    run_id=run_id.strip(),
                    feedback=feedback.strip(),
                    on_event=on_event,
                )

                report_text = run.report_markdown or accumulated_text or "No report"
                download_path = prepare_download(
                    run.report_markdown, f"{run.run_id}_v{run.version}"
                )
                yield {
                    revised_status: run.status.value,
                    revised_report_output: report_text,
                    revised_cost_output: calculate_cost(run.usage),
                    revised_download_btn: gr.DownloadButton(
                        value=download_path, visible=download_path is not None
                    ),
                }

            except ValueError as e:
                yield {
                    revised_status: f"Error: {e}",
                    revised_report_output: "",
                    revised_cost_output: "",
                    revised_download_btn: gr.DownloadButton(visible=False),
                }
            except Exception as e:
                yield {
                    revised_status: f"Error: {e}",
                    revised_report_output: accumulated_text or "",
                    revised_cost_output: "",
                    revised_download_btn: gr.DownloadButton(visible=False),
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

        # Wire up events
        start_btn.click(
            fn=run_research,
            inputs=[
                topic_input,
                timeframe_input,
                region_input,
                depth_dropdown,
                max_words_input,
                focus_input,
            ],
            outputs=[
                status_output,
                run_id_output,
                report_output,
                cost_output,
                download_btn,
            ],
        )

        load_btn.click(
            fn=load_run_for_revision,
            inputs=[revise_run_id],
            outputs=[current_report_display],
        )

        revise_btn.click(
            fn=do_revision,
            inputs=[revise_run_id, feedback_input],
            outputs=[
                revised_status,
                revised_report_output,
                revised_cost_output,
                revised_download_btn,
            ],
        )

        refresh_btn.click(
            fn=refresh_runs,
            outputs=[runs_table],
        )

    return demo


def launch() -> None:
    """Launch the Gradio interface."""
    demo = create_ui()
    demo.launch()


if __name__ == "__main__":
    launch()
