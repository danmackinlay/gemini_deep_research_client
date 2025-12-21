"""CLI interface for the Deep Research client."""

import json
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from deep_research_app.workflow import ResearchWorkflow, ResearchConstraints
from deep_research_app.storage import RunStorage
from deep_research_app.deep_research import DeepResearchClient
from deep_research_app.models import InteractionStatus, UsageMetadata

app = typer.Typer(
    name="deep-research",
    help="CLI client for Google Gemini Deep Research API",
    add_completion=False,
)
console = Console()

# Global state for options
_show_thoughts: bool = False
_debug_chunks: bool = False


@app.callback()
def main_callback(
    thoughts: bool = typer.Option(
        False,
        "--show-thoughts",
        "-t",
        help="Show thinking summaries during streaming",
    ),
    debug_chunks: bool = typer.Option(
        False,
        "--debug-chunks",
        help="Log raw API chunks to debug_chunks.jsonl for citation debugging",
    ),
) -> None:
    """Global options for deep-research commands."""
    global _show_thoughts, _debug_chunks
    _show_thoughts = thoughts
    _debug_chunks = debug_chunks


@app.command("new")
def new_research(
    topic: str = typer.Argument(..., help="Research topic or question"),
    timeframe: Optional[str] = typer.Option(
        None,
        "--timeframe",
        "-f",
        help="Time period constraint (e.g., '2020-2024')",
    ),
    region: Optional[str] = typer.Option(
        None,
        "--region",
        "-r",
        help="Geographic focus",
    ),
    max_words: Optional[int] = typer.Option(
        None,
        "--max-words",
        "-w",
        help="Maximum report length in words",
    ),
    depth: str = typer.Option(
        "comprehensive",
        "--depth",
        "-d",
        help="Research depth: brief, moderate, comprehensive",
    ),
    focus: Optional[str] = typer.Option(
        None,
        "--focus",
        help="Comma-separated focus areas",
    ),
) -> None:
    """
    Start a new research run on a topic.

    Example:
        deep-research new "Impact of AI on healthcare" --timeframe "2020-2024"
    """
    workflow = ResearchWorkflow()

    constraints = ResearchConstraints(
        timeframe=timeframe,
        region=region,
        depth=depth,
        max_words=max_words,
        focus_areas=focus.split(",") if focus else None,
    )

    console.print(
        Panel(f"[bold]Starting research on:[/bold] {topic}", title="Deep Research")
    )

    def on_event(event_type: str, text: str) -> None:
        if event_type == "start":
            console.print(f"[dim]{text}[/dim]")
        elif event_type == "text":
            console.print(text, end="")
        elif event_type == "thought" and _show_thoughts:
            console.print(f"\n[yellow][THOUGHT][/yellow] {text}")
        elif event_type == "complete":
            console.print("\n[green]Research complete![/green]")
        elif event_type == "error":
            console.print(f"\n[red]Error: {text}[/red]")

    def on_status(status: str) -> None:
        console.print(f"[dim]Status: {status}[/dim]")

    # Debug callback: collect chunks in memory, write to file after we have run_id
    debug_chunks: list[dict[str, Any]] = []

    def on_debug(chunk_dict: dict[str, Any]) -> None:
        debug_chunks.append(chunk_dict)

    try:
        run = workflow.run_initial_research(
            topic=topic,
            constraints=constraints,
            on_event=on_event,
            on_status=on_status,
            on_debug=on_debug if _debug_chunks else None,
        )

        # Write debug chunks to file if enabled
        if _debug_chunks and debug_chunks:
            debug_path = Path("runs") / run.run_id / "debug_chunks.jsonl"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            with debug_path.open("w", encoding="utf-8") as f:
                for chunk in debug_chunks:
                    f.write(json.dumps(chunk) + "\n")
            console.print(f"[dim]Debug chunks saved to: {debug_path}[/dim]")

        if run.status == InteractionStatus.COMPLETED:
            console.print(
                f"\n[green]Report saved to:[/green] runs/{run.run_id}/report_v{run.version}.md"
            )
            console.print(f"[dim]Run ID: {run.run_id}[/dim]")
            if run.usage:
                console.print(f"[dim]{run.usage.format_cost()}[/dim]")
        elif run.status == InteractionStatus.INTERRUPTED:
            console.print("\n[yellow]Research interrupted.[/yellow]")
            console.print(
                f"Run `deep-research status {run.interaction_id}` to check completion."
            )
            console.print(f"Run `deep-research resume {run.run_id}` to resume.")
        else:
            console.print(f"\n[red]Research ended with status: {run.status}[/red]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        console.print("The research may still be running on Google's servers.")
        raise typer.Exit(1)


@app.command("revise")
def revise_research(
    run_id: str = typer.Argument(..., help="Run ID to revise"),
    feedback: Optional[str] = typer.Option(
        None,
        "--feedback",
        "-m",
        help="Feedback for revision (or enter interactively)",
    ),
) -> None:
    """
    Revise an existing research run based on feedback.

    Example:
        deep-research revise abc123 --feedback "Add more focus on economic impacts"
    """
    storage = RunStorage()
    workflow = ResearchWorkflow(storage)

    # Load and show current report summary
    run = storage.load_latest_run(run_id)
    if not run:
        console.print(f"[red]Run not found: {run_id}[/red]")
        raise typer.Exit(1)

    if run.status != InteractionStatus.COMPLETED:
        console.print(f"[red]Cannot revise: run status is {run.status}[/red]")
        raise typer.Exit(1)

    # Show report summary
    summary = run.report_markdown[:500] + "..." if run.report_markdown else "No report"
    console.print(
        Panel(
            f"[bold]Current report (v{run.version}):[/bold]\n{summary}",
            title=f"Run {run_id}",
        )
    )

    # Get feedback interactively if not provided
    if not feedback:
        feedback = typer.prompt("Enter your feedback for revision")

    console.print(f"\n[bold]Revising with feedback:[/bold] {feedback}\n")

    def on_event(event_type: str, text: str) -> None:
        if event_type == "text":
            console.print(text, end="")
        elif event_type == "thought" and _show_thoughts:
            console.print(f"\n[yellow][THOUGHT][/yellow] {text}")
        elif event_type == "complete":
            console.print("\n[green]Revision complete![/green]")

    try:
        revised_run = workflow.revise_research(
            run_id=run_id,
            feedback=feedback,
            on_event=on_event,
        )

        if revised_run.status == InteractionStatus.COMPLETED:
            console.print(
                f"\n[green]Revised report saved to:[/green] "
                f"runs/{run_id}/report_v{revised_run.version}.md"
            )
            if revised_run.usage:
                console.print(f"[dim]{revised_run.usage.format_cost()}[/dim]")
        else:
            console.print(
                f"\n[red]Revision ended with status: {revised_run.status}[/red]"
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command("show")
def show_report(
    run_id: str = typer.Argument(..., help="Run ID to show"),
    version: Optional[int] = typer.Option(
        None,
        "--version",
        "-v",
        help="Specific version (default: latest)",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Show raw Markdown without formatting",
    ),
) -> None:
    """
    Display a research report.

    Example:
        deep-research show abc123
        deep-research show abc123 --version 1
    """
    storage = RunStorage()

    if version:
        run = storage.load_run_version(run_id, version)
    else:
        run = storage.load_latest_run(run_id)

    if not run:
        console.print(f"[red]Run not found: {run_id}[/red]")
        raise typer.Exit(1)

    if not run.report_markdown:
        console.print("[yellow]No report available (run may be incomplete)[/yellow]")
        raise typer.Exit(1)

    # Show usage info from metadata
    meta = storage._load_metadata(run_id)
    if meta:
        version_info = next(
            (v for v in meta.versions if v["version"] == run.version),
            None,
        )
        if version_info and version_info.get("usage"):
            usage = UsageMetadata.from_dict(version_info["usage"])
            console.print(f"[dim]{usage.format_cost()}[/dim]\n")

    if raw:
        console.print(run.report_markdown)
    else:
        console.print(Markdown(run.report_markdown))


@app.command("status")
def check_status(
    interaction_id: str = typer.Argument(..., help="Gemini interaction ID to check"),
) -> None:
    """
    Check the status of a Gemini interaction.

    Example:
        deep-research status int_abc123xyz
    """
    client = DeepResearchClient()

    status, text = client.get_interaction_status(interaction_id)

    console.print(f"[bold]Interaction:[/bold] {interaction_id}")
    console.print(f"[bold]Status:[/bold] {status.value}")

    if status == InteractionStatus.COMPLETED and text:
        console.print(f"\n[green]Report is ready ({len(text)} characters)[/green]")
        if typer.confirm("Display the report?"):
            console.print(Markdown(text))


@app.command("list")
def list_runs() -> None:
    """List all research runs."""
    storage = RunStorage()
    runs = storage.list_runs()

    if not runs:
        console.print("[dim]No runs found[/dim]")
        return

    console.print("[bold]Research Runs:[/bold]\n")
    for meta in runs:
        topic_preview = meta.topic[:60] + "..." if len(meta.topic) > 60 else meta.topic
        console.print(
            f"  [cyan]{meta.run_id}[/cyan] (v{meta.latest_version}) - {topic_preview}"
        )

        # Get usage from latest version
        latest_version = next(
            (v for v in meta.versions if v["version"] == meta.latest_version),
            None,
        )
        cost_info = ""
        if latest_version and latest_version.get("usage"):
            usage = UsageMetadata.from_dict(latest_version["usage"])
            cost_info = f" | ${usage.calculate_cost():.4f}"

        console.print(f"    Created: {meta.created_at}{cost_info}")


@app.command("resume")
def resume_run(
    run_id: str = typer.Argument(..., help="Run ID to resume"),
) -> None:
    """
    Resume an interrupted research run.

    Example:
        deep-research resume abc123
    """
    workflow = ResearchWorkflow()

    def on_event(event_type: str, text: str) -> None:
        if event_type == "text":
            console.print(text, end="")
        elif event_type == "complete":
            console.print("\n[green]Research complete![/green]")

    def on_status(status: str) -> None:
        console.print(f"[dim]Status: {status}[/dim]")

    try:
        run = workflow.resume_interrupted(
            run_id,
            on_event=on_event,
            on_status=on_status,
        )

        if run.status == InteractionStatus.COMPLETED:
            console.print(
                f"\n[green]Report saved to:[/green] runs/{run.run_id}/report_v{run.version}.md"
            )
        else:
            console.print(f"\n[yellow]Status: {run.status}[/yellow]")

    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
