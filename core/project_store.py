"""
Global in-memory store: project_id → filesystem path.
Lets terminal sessions access generated project files after /agent completes.
"""
from __future__ import annotations

import threading
from pathlib import Path

_store: dict[str, Path] = {}
_lock = threading.Lock()


def register(project_id: str, path: Path) -> None:
    with _lock:
        _store[project_id] = path


def get(project_id: str) -> Path | None:
    with _lock:
        return _store.get(project_id)


def list_all() -> list[dict]:
    with _lock:
        return [{"project_id": pid, "path": str(p), "exists": p.exists()}
                for pid, p in _store.items()]
