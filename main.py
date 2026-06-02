"""
Vire Agent Backend v2.0 — FastAPI entry point
==============================================
POST /analyze               → plan preview + required inputs
POST /agent                 → SSE streaming agent run
POST /agent/tool            → single tool call
GET  /usage                 → per-key usage stats for all tiers
GET  /project/{id}/files    → list generated files
GET  /project/{id}/download → download project as ZIP
GET  /projects              → list all known project sessions
POST /terminal              → create terminal session for a project
POST /terminal/{id}/exec    → SSE streaming command execution
POST /terminal/{id}/ai      → AI terminal assistant
GET  /terminal/{id}         → session info
GET  /health                → health check
GET  /                      → info
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents import orchestrator
from agents.assistant import ask_assistant
from agents.planner import plan as run_plan
from core.events import EventType, Timer, make_event
from core.gemini import RateLimitError, get_lite_rotator
from core import project_store
from tools import terminal as term

app = FastAPI(
    title="Vire — AI Agent Backend",
    description="Builds Telegram bots, FastAPI apps, React web apps, and Expo mobile apps",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request/Response models ───────────────────────────────────────────────────

class AgentRequest(BaseModel):
    prompt: str
    inputs: dict[str, str] | None = None
    tier: str = "mini"


class AnalyzeRequest(BaseModel):
    prompt: str
    tier: str = "mini"


class ToolRequest(BaseModel):
    tool: str
    params: dict[str, Any]
    tier: str = "mini"


class TerminalCreateRequest(BaseModel):
    project_id: str


class TerminalExecRequest(BaseModel):
    command: str
    timeout: int = 30


class TerminalAIRequest(BaseModel):
    message: str


# ── Root & Health ─────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    try:
        key_count = get_lite_rotator().total_keys
    except Exception:
        key_count = 0
    max_keys_loaded = bool(os.getenv("VIRE_MAX_KEYS"))
    return {
        "status": "ok",
        "service": "Vire Agent Backend",
        "version": "2.0.0",
        "models": {
            "mini": {"model": os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"), "keys": key_count},
            "core": {"planner": os.getenv("GEMINI_MAX_MODEL", "gemini-3.5-flash"), "others": "gemini-3.1-flash-lite"},
            "max": {"model": os.getenv("GEMINI_MAX_MODEL", "gemini-3.5-flash"), "dedicated_keys": max_keys_loaded},
        },
        "endpoints": {
            "POST /analyze": "Plan preview + required inputs",
            "POST /agent": "SSE streaming agent run (tier = mini|core|max)",
            "GET /usage": "Per-key usage stats (JSON)",
            "GET /project/{id}/download": "Download project as ZIP",
            "POST /terminal": "Create terminal session",
            "POST /terminal/{id}/exec": "SSE command execution",
            "POST /terminal/{id}/ai": "AI terminal assistant",
        },
        "gemini_keys_loaded": key_count,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": int(time.time())}


# ── Usage stats ───────────────────────────────────────────────────────────────

@app.get("/usage")
async def usage():
    """Real-time per-key usage stats for all model tiers."""
    from core.rate_limiter import get_core_limiter, get_max_limiter

    max_limiter = get_max_limiter()
    max_usages = max_limiter.all_usage()
    max_status = max_limiter.overall_status()

    core_lim = get_core_limiter()
    core_usage = core_lim.usage(1)

    limits = {"per_minute": 5, "per_day": 20}

    return {
        "tiers": {
            "mini": {
                "status": "available",
                "model": os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
                "note": "No rate limits tracked (standard quota)",
            },
            "core": {
                "status": core_usage.status,
                "model": os.getenv("GEMINI_MAX_MODEL", "gemini-3.5-flash"),
                "planner_only": True,
                "limits": limits,
                "used_per_minute": core_usage.used_per_minute,
                "remaining_per_minute": core_usage.remaining_per_minute,
                "used_per_day": core_usage.used_per_day,
                "remaining_per_day": core_usage.remaining_per_day,
                "reset_minute_ts": core_usage.reset_minute_ts,
                "reset_day_ts": core_usage.reset_day_ts,
            },
            "max": {
                "status": max_status,
                "model": os.getenv("GEMINI_MAX_MODEL", "gemini-3.5-flash"),
                "limits": limits,
                "keys": [
                    {
                        "index": u.index,
                        "status": u.status,
                        "used_per_minute": u.used_per_minute,
                        "remaining_per_minute": u.remaining_per_minute,
                        "used_per_day": u.used_per_day,
                        "remaining_per_day": u.remaining_per_day,
                        "reset_minute_ts": u.reset_minute_ts,
                        "reset_day_ts": u.reset_day_ts,
                    }
                    for u in max_usages
                ],
            },
        }
    }


# ── Analyze ───────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    try:
        plan_text, tokens = await run_plan(req.prompt, tier=req.tier)
        missing = orchestrator._detect_missing_inputs(plan_text, {})
        return {
            "plan_preview": plan_text[:600],
            "required_inputs": missing,
            "tokens_used": tokens,
            "ready_to_run": len(missing) == 0,
            "tier": req.tier,
        }
    except RateLimitError as e:
        raise HTTPException(
            status_code=429,
            detail={"error": str(e), "tier": e.tier, "reason": e.reason,
                    "suggestion": "Try tier=core or tier=mini"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Agent SSE run ─────────────────────────────────────────────────────────────

@app.post("/agent")
async def run_agent(req: AgentRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt cannot be empty")

    tier = req.tier if req.tier in ("mini", "core", "max") else "mini"

    async def event_stream() -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in orchestrator.run(
                prompt=req.prompt,
                user_inputs=req.inputs or {},
                tier=tier,
            ):
                yield chunk.encode("utf-8")
        except Exception as exc:
            err = make_event(EventType.ERROR, content=f"Server error: {exc}",
                             time_ms=0, tokens_used=0, error_type=type(exc).__name__)
            yield err.encode("utf-8")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Agent tool ────────────────────────────────────────────────────────────────

@app.post("/agent/tool")
async def call_tool(req: ToolRequest):
    tool, params, tier = req.tool, req.params, req.tier

    if tool == "plan":
        text, tokens = await run_plan(params.get("description", ""), tier=tier)
        return {"result": text, "tokens_used": tokens}

    elif tool == "ask_assistant":
        text, tokens = await ask_assistant(
            params.get("task", ""), params.get("context", ""), tier=tier)
        return {"result": text, "tokens_used": tokens}

    elif tool == "execute_command":
        from pathlib import Path
        from tools.exec_tools import execute_command
        project_id = params.get("project_id")
        command = params.get("command", "")
        if not command:
            raise HTTPException(status_code=400, detail="command required")
        cwd = Path(f"/tmp/agent_{project_id}") if project_id else Path("/tmp")
        out, code = await execute_command(command, cwd)
        return {"output": out, "exit_code": code}

    raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")


# ── Project files & download ──────────────────────────────────────────────────

@app.get("/projects")
async def list_projects():
    return {"projects": project_store.list_all()}


@app.get("/project/{project_id}/files")
async def list_project_files(project_id: str):
    path = project_store.get(project_id)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    files = []
    for f in sorted(path.rglob("*")):
        if f.is_file() and not any(p in f.parts for p in ("__pycache__", ".pip_packages", "node_modules", ".expo")):
            rel = str(f.relative_to(path))
            files.append({"path": rel, "size": f.stat().st_size})
    return {"project_id": project_id, "root": str(path), "files": files}


@app.get("/project/{project_id}/download")
async def download_project(project_id: str):
    """Download all generated project files as a ZIP archive."""
    path = project_store.get(project_id)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(path.rglob("*")):
            if f.is_file() and not any(
                p in f.parts for p in ("__pycache__", ".pip_packages", "node_modules", ".expo", ".git")
            ):
                rel = str(f.relative_to(path))
                zf.write(f, rel)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=vire_{project_id}.zip"},
    )


# ── Terminal sessions ─────────────────────────────────────────────────────────

@app.post("/terminal")
async def create_terminal(req: TerminalCreateRequest):
    """Create a new terminal session linked to a generated project."""
    path = project_store.get(req.project_id)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{req.project_id}' not found")
    session_id = term.create_session(req.project_id)
    session = term.get_session(session_id)
    return {
        "session_id": session_id,
        "project_id": req.project_id,
        "cwd": session["cwd"] if session else str(path),
    }


@app.get("/terminal/{session_id}")
async def get_terminal(session_id: str):
    session = term.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "project_id": session["project_id"],
        "cwd": session["cwd"],
        "commands_run": len(session["history"]),
        "created_at": session["created_at"],
    }


@app.post("/terminal/{session_id}/exec")
async def terminal_exec(session_id: str, req: TerminalExecRequest):
    """Stream command output via SSE."""
    session = term.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def stream() -> AsyncGenerator[bytes, None]:
        async for chunk in term.exec_stream(session_id, req.command, req.timeout):
            yield chunk.encode("utf-8")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/terminal/{session_id}/ai")
async def terminal_ai(session_id: str, req: TerminalAIRequest):
    """AI assistant with project + terminal history context."""
    session = term.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Load project files for context
    project_files: dict[str, str] = {}
    path = project_store.get(session["project_id"])
    if path and path.exists():
        for f in sorted(path.rglob("*")):
            if f.is_file() and f.suffix in (".py", ".ts", ".tsx", ".js", ".json") \
               and not any(p in f.parts for p in ("node_modules", "__pycache__", ".pip_packages")):
                try:
                    project_files[str(f.relative_to(path))] = f.read_text(errors="replace")
                except Exception:
                    pass
                if len(project_files) >= 6:
                    break

    answer = await term.ask_terminal_ai(session_id, req.message, project_files)
    return {
        "session_id": session_id,
        "question": req.message,
        "answer": answer,
        "model": "gemini-3.1-flash-lite",
    }


# ── Vercel deploy ─────────────────────────────────────────────────────────────

@app.post("/deploy/vercel/{project_id}")
async def deploy_vercel(project_id: str):
    """
    Deploy a TypeScript project to Vercel.
    Requires VERCEL_TOKEN env var.
    Returns SSE stream with build logs and final URL.
    """
    from tools.vercel import deploy_to_vercel

    path = project_store.get(project_id)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    if not os.getenv("VERCEL_TOKEN"):
        raise HTTPException(
            status_code=503,
            detail="VERCEL_TOKEN is not configured. Add it to Railway environment variables.",
        )

    async def stream() -> AsyncGenerator[bytes, None]:
        async for chunk in deploy_to_vercel(project_id, path):
            yield chunk.encode("utf-8")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/deploy/vercel/status")
async def vercel_status():
    """Check if Vercel deployment is configured."""
    has_token = bool(os.getenv("VERCEL_TOKEN"))
    return {
        "configured": has_token,
        "message": "Ready" if has_token else "Set VERCEL_TOKEN in Railway to enable deployments",
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
