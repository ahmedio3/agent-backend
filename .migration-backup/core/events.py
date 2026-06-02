"""SSE event helpers and type definitions."""
from __future__ import annotations

import json
import time
from enum import Enum
from typing import Any


class EventType(str, Enum):
    LOG = "log"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    FILE_CREATED = "file_created"
    FILE_UPDATED = "file_updated"
    COMMAND_OUTPUT = "command_output"
    NEED_INPUT = "need_input"
    AGENT_THINKING = "agent_thinking"
    DEBUG_START = "debug_start"
    DEBUG_END = "debug_end"
    PLAN = "plan"
    ERROR = "error"
    DONE = "done"
    STEP = "step"


def make_event(
    event_type: EventType | str,
    content: str = "",
    time_ms: int = 0,
    tokens_used: int = 0,
    **extra: Any,
) -> str:
    """Format a dict as an SSE data line."""
    data: dict[str, Any] = {
        "type": event_type,
        "content": content,
        "time_ms": time_ms,
        "tokens_used": tokens_used,
    }
    data.update(extra)
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


class Timer:
    """Simple wall-clock timer."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)
