"""
Terminal sessions — sandboxed command execution with AI assistant.

Each session is tied to a generated project. Commands run inside the
project's /tmp directory. The AI assistant (gemini-3.1-flash-lite) has
full context of the project files and command history.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator

from core.project_store import get as get_project_path

# ── In-memory session store ───────────────────────────────────────────────────

_sessions: dict[str, dict] = {}
# session_id → {project_id, cwd, history: [{cmd, output, ts}], created_at}


def create_session(project_id: str) -> str:
    session_id = str(uuid.uuid4())[:8]
    project_path = get_project_path(project_id)
    cwd = str(project_path) if project_path else "/tmp"
    _sessions[session_id] = {
        "session_id": session_id,
        "project_id": project_id,
        "cwd": cwd,
        "history": [],
        "created_at": time.time(),
    }
    return session_id


def get_session(session_id: str) -> dict | None:
    return _sessions.get(session_id)


def list_sessions() -> list[dict]:
    return [
        {
            "session_id": s["session_id"],
            "project_id": s["project_id"],
            "commands_run": len(s["history"]),
            "created_at": s["created_at"],
        }
        for s in _sessions.values()
    ]


# ── Command execution with SSE streaming ─────────────────────────────────────

BLOCKED_COMMANDS = ("rm -rf /", "shutdown", "reboot", "dd if=", "mkfs")


async def exec_stream(
    session_id: str,
    command: str,
    timeout: int = 30,
) -> AsyncGenerator[str, None]:
    """
    Execute a shell command and yield SSE-formatted lines.
    Yields: data: {"type": "output"|"error"|"done", "line": "...", "exit_code": int}
    """
    import json

    session = get_session(session_id)
    if session is None:
        yield f'data: {json.dumps({"type":"error","line":"Session not found"})}\n\n'
        return

    # Safety check
    cmd_lower = command.strip().lower()
    if any(blocked in cmd_lower for blocked in BLOCKED_COMMANDS):
        yield f'data: {json.dumps({"type":"error","line":"Command blocked for safety"})}\n\n'
        return

    cwd = session["cwd"]
    env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1"}

    yield f'data: {json.dumps({"type":"start","command":command})}\n\n'

    output_lines: list[str] = []
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=env,
        )

        async def read_lines() -> None:
            assert proc.stdout is not None
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                output_lines.append(line)
                yield f'data: {json.dumps({"type":"output","line":line})}\n\n'

        try:
            async for chunk in read_lines():
                yield chunk
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            exit_code = -1
            yield f'data: {json.dumps({"type":"output","line":f"[timeout after {timeout}s]"})}\n\n'

    except Exception as exc:
        exit_code = 1
        yield f'data: {json.dumps({"type":"error","line":str(exc)})}\n\n'

    # Store in history
    full_output = "\n".join(output_lines)
    session["history"].append({
        "cmd": command,
        "output": full_output[:2000],
        "exit_code": exit_code,
        "ts": time.time(),
    })

    yield f'data: {json.dumps({"type":"done","exit_code":exit_code})}\n\n'


# ── AI Terminal Assistant ─────────────────────────────────────────────────────

async def ask_terminal_ai(
    session_id: str,
    message: str,
    project_files: dict[str, str] | None = None,
) -> str:
    """
    AI assistant with full project + terminal history context.
    Uses gemini-3.1-flash-lite (mini model, no rate limits for terminal).
    Returns the AI response as plain text.
    """
    from core.gemini import call_gemini_text

    session = get_session(session_id)
    if session is None:
        return "Session not found. Create a new terminal session first."

    # Build context
    history_text = ""
    for entry in session["history"][-5:]:  # Last 5 commands
        history_text += f"\n$ {entry['cmd']}\n{entry['output'][:300]}\n(exit: {entry['exit_code']})"

    files_text = ""
    if project_files:
        for fname, content in list(project_files.items())[:4]:
            files_text += f"\n--- {fname} ---\n{content[:400]}\n"

    system = """أنت مساعد ذكي متكامل مع Terminal لمشروع برمجي.
لديك كامل السياق:
- ملفات المشروع المولّدة
- سجل الأوامر المنفّذة ونتائجها

مهمتك:
- تفسير أخطاء Terminal وإصلاحها
- اقتراح الأوامر الصحيحة
- الإجابة على أسئلة تتعلق بالمشروع
- تقديم أمثلة كود عند الطلب

أجب بإيجاز ودقة. للأوامر: استخدم ```bash ... ```"""

    prompt = f"""سياق الـ Terminal:
جلسة: {session_id} | مشروع: {session['project_id']}
المجلد: {session['cwd']}

سجل الأوامر الأخيرة:{history_text or ' (لا يوجد بعد)'}

ملفات المشروع:{files_text or ' (لا يوجد)'}

سؤال المستخدم:
{message}"""

    text, _ = await call_gemini_text(
        prompt=prompt,
        system_instruction=system,
        temperature=0.4,
    )
    return text
