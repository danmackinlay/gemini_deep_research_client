"""Persistence layer for research runs."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from deep_research_app.config import get_settings
from deep_research_app.models import ResearchRun, RunMetadata, InteractionStatus


class RunStorage:
    """Manages persistence of research runs."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base_dir = base_dir or get_settings().runs_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def get_run_dir(self, run_id: str) -> Path:
        """Get/create the directory for a run."""
        run_dir = self._base_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def save_run(self, run: ResearchRun) -> Path:
        """
        Save a research run (prompt and report for its version).

        Creates/updates:
        - runs/{run_id}/prompt_v{N}.md
        - runs/{run_id}/report_v{N}.md (if report exists)
        - runs/{run_id}/meta.json
        """
        run_dir = self.get_run_dir(run.run_id)

        # Save prompt
        prompt_path = run_dir / f"prompt_v{run.version}.md"
        prompt_path.write_text(run.prompt_text, encoding="utf-8")

        # Save report if available
        if run.report_markdown:
            report_path = run_dir / f"report_v{run.version}.md"
            report_path.write_text(run.report_markdown, encoding="utf-8")

        # Update metadata
        self._update_metadata(run)

        return run_dir

    def save_stream_state(
        self,
        run_id: str,
        interaction_id: str,
        last_event_id: str,
        partial_text: str,
    ) -> None:
        """
        Save streaming state for potential resume.

        Creates runs/{run_id}/stream_state.json
        """
        run_dir = self.get_run_dir(run_id)
        state_path = run_dir / "stream_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "interaction_id": interaction_id,
                    "last_event_id": last_event_id,
                    "partial_text": partial_text,
                    "saved_at": datetime.now().isoformat(),
                }
            ),
            encoding="utf-8",
        )

    def load_stream_state(self, run_id: str) -> Optional[dict]:
        """Load saved stream state for resume."""
        state_path = self.get_run_dir(run_id) / "stream_state.json"
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
        return None

    def clear_stream_state(self, run_id: str) -> None:
        """Clear stream state after successful completion."""
        state_path = self.get_run_dir(run_id) / "stream_state.json"
        if state_path.exists():
            state_path.unlink()

    def load_latest_run(self, run_id: str) -> Optional[ResearchRun]:
        """Load the latest version of a run."""
        meta = self._load_metadata(run_id)
        if not meta:
            return None

        version = meta.latest_version
        run_dir = self._base_dir / run_id

        prompt_path = run_dir / f"prompt_v{version}.md"
        report_path = run_dir / f"report_v{version}.md"

        version_info = next(
            (v for v in meta.versions if v["version"] == version),
            None,
        )

        return ResearchRun(
            run_id=run_id,
            interaction_id=version_info["interaction_id"] if version_info else "",
            version=version,
            prompt_text=(
                prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
            ),
            report_markdown=(
                report_path.read_text(encoding="utf-8")
                if report_path.exists()
                else None
            ),
            created_at=(
                datetime.fromisoformat(version_info["created_at"])
                if version_info
                else datetime.now()
            ),
            feedback=version_info.get("feedback") if version_info else None,
            previous_interaction_id=(
                version_info.get("previous_interaction_id") if version_info else None
            ),
            status=(
                InteractionStatus(version_info["status"])
                if version_info
                else InteractionStatus.PENDING
            ),
        )

    def load_run_version(self, run_id: str, version: int) -> Optional[ResearchRun]:
        """Load a specific version of a run."""
        meta = self._load_metadata(run_id)
        if not meta:
            return None

        run_dir = self._base_dir / run_id
        prompt_path = run_dir / f"prompt_v{version}.md"
        report_path = run_dir / f"report_v{version}.md"

        if not prompt_path.exists():
            return None

        version_info = next(
            (v for v in meta.versions if v["version"] == version),
            None,
        )

        return ResearchRun(
            run_id=run_id,
            interaction_id=version_info["interaction_id"] if version_info else "",
            version=version,
            prompt_text=prompt_path.read_text(encoding="utf-8"),
            report_markdown=(
                report_path.read_text(encoding="utf-8")
                if report_path.exists()
                else None
            ),
            created_at=(
                datetime.fromisoformat(version_info["created_at"])
                if version_info
                else datetime.now()
            ),
            feedback=version_info.get("feedback") if version_info else None,
            previous_interaction_id=(
                version_info.get("previous_interaction_id") if version_info else None
            ),
            status=(
                InteractionStatus(version_info["status"])
                if version_info
                else InteractionStatus.PENDING
            ),
        )

    def list_runs(self) -> list[RunMetadata]:
        """List all runs with their metadata."""
        runs = []
        for run_dir in self._base_dir.iterdir():
            if run_dir.is_dir():
                meta = self._load_metadata(run_dir.name)
                if meta:
                    runs.append(meta)
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def _update_metadata(self, run: ResearchRun) -> None:
        """Update meta.json for a run."""
        run_dir = self.get_run_dir(run.run_id)
        meta_path = run_dir / "meta.json"

        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = {
                "run_id": run.run_id,
                "topic": run.prompt_text[:100],
                "created_at": run.created_at.isoformat(),
                "versions": [],
                "latest_version": 0,
            }

        # Add or update version info
        version_info = {
            "version": run.version,
            "interaction_id": run.interaction_id,
            "created_at": run.created_at.isoformat(),
            "status": run.status.value,
            "feedback": run.feedback,
            "previous_interaction_id": run.previous_interaction_id,
            "usage": {
                "prompt_tokens": run.usage.prompt_tokens,
                "output_tokens": run.usage.output_tokens,
                "total_tokens": run.usage.total_tokens,
                "thinking_tokens": run.usage.thinking_tokens,
            }
            if run.usage
            else None,
        }

        # Replace existing version or append
        existing_idx = next(
            (i for i, v in enumerate(meta["versions"]) if v["version"] == run.version),
            None,
        )
        if existing_idx is not None:
            meta["versions"][existing_idx] = version_info
        else:
            meta["versions"].append(version_info)

        meta["latest_version"] = max(v["version"] for v in meta["versions"])

        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _load_metadata(self, run_id: str) -> Optional[RunMetadata]:
        """Load metadata for a run."""
        meta_path = self._base_dir / run_id / "meta.json"
        if not meta_path.exists():
            return None

        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return RunMetadata(**data)

    def save_sources(self, run_id: str, version: int, sources: dict) -> None:
        """Save sources.json for a version."""
        run_dir = self.get_run_dir(run_id)
        path = run_dir / f"sources_v{version}.json"

        # Convert SourceInfo objects to dicts if needed
        sources_dict = {}
        for num, src in sources.items():
            if hasattr(src, "title"):
                sources_dict[num] = {
                    "title": src.title,
                    "url": src.url,
                    "final_url": src.final_url,
                }
            else:
                sources_dict[num] = src

        path.write_text(json.dumps(sources_dict, indent=2), encoding="utf-8")

    def load_sources(self, run_id: str, version: int) -> Optional[dict]:
        """Load sources.json for a version."""
        path = self._base_dir / run_id / f"sources_v{version}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
