"""Project isolation — each run lives in its own /tmp directory."""
from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, Optional


class Project:
    """Manages an isolated temporary workspace for one agent run."""

    def __init__(self, project_id: Optional[str] = None) -> None:
        self.project_id: str = project_id or str(uuid.uuid4())[:8]
        self.root: Path = Path("/tmp") / f"agent_{self.project_id}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.files: Dict[str, str] = {}  # relative path -> content
        self.created_at: float = time.time()
        self.step_log: list[dict] = []
        # Register in global store so terminal sessions can find this project
        from core.project_store import register
        register(self.project_id, self.root)

    # ── File operations ──────────────────────────────────────────────────────

    def write_file(self, relative_path: str, content: str) -> Path:
        """Write (or overwrite) a file inside the project directory."""
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.files[relative_path] = content
        return target

    def read_file(self, relative_path: str) -> str:
        """Read a file from the project directory. Raises FileNotFoundError."""
        target = self.root / relative_path
        if not target.exists():
            raise FileNotFoundError(f"File not found: {relative_path}")
        content = target.read_text(encoding="utf-8")
        self.files[relative_path] = content
        return content

    def patch_file(self, relative_path: str, old_snippet: str, new_snippet: str) -> str:
        """Replace exactly one occurrence of old_snippet with new_snippet."""
        content = self.read_file(relative_path)
        if old_snippet not in content:
            raise ValueError(
                f"Snippet not found in {relative_path}.\n"
                f"Snippet: {old_snippet[:100]}..."
            )
        patched = content.replace(old_snippet, new_snippet, 1)
        self.write_file(relative_path, patched)
        return patched

    def list_files(self) -> list[str]:
        """Return all relative file paths tracked so far."""
        return list(self.files.keys())

    def get_all_contents(self) -> Dict[str, str]:
        """Reload and return all file contents."""
        result: Dict[str, str] = {}
        for rel in self.files:
            try:
                result[rel] = self.read_file(rel)
            except FileNotFoundError:
                pass
        return result

    def file_exists(self, relative_path: str) -> bool:
        return (self.root / relative_path).exists()

    # ── Logging ──────────────────────────────────────────────────────────────

    def log_step(self, step: dict) -> None:
        self.step_log.append({**step, "ts": time.time()})

    # ── Cleanup ──────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Remove the temporary project directory."""
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def __repr__(self) -> str:
        return f"<Project id={self.project_id} root={self.root} files={len(self.files)}>"
