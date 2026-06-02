"""
Vercel deployment tool — deploys TypeScript/React projects via Vercel API.

Required env var: VERCEL_TOKEN
Optional env vars:
  VERCEL_TEAM_ID  — deploy to a team (optional)
  VERCEL_ORG_ID   — same as team ID (alias)

Flow:
  1. Upload all project files to Vercel
  2. Create a deployment
  3. Stream build events (SSE) back to client
  4. Return final deployment URL

Fix: SSE keepalive pings (": ping\\n\\n") sent every ~10 seconds to prevent
Railway/proxy timeout on long-running builds.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import AsyncGenerator


_API = "https://api.vercel.com"

FRAMEWORK_HINTS: dict[str, str] = {
    "vite.config.ts": "vite",
    "vite.config.js": "vite",
    "next.config.ts": "nextjs",
    "next.config.js": "nextjs",
    "nuxt.config.ts": "nuxt",
    "angular.json": "angular",
    "remix.config.js": "remix",
}

SKIP_PATHS = frozenset(
    [".git", "node_modules", ".expo", "__pycache__", ".pip_packages", ".tsbuildinfo"]
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _token() -> str:
    t = os.getenv("VERCEL_TOKEN", "").strip()
    if not t:
        raise RuntimeError("VERCEL_TOKEN environment variable is not set.")
    return t


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }


def _detect_framework(files: dict[str, str]) -> str:
    for fname, framework in FRAMEWORK_HINTS.items():
        if fname in files:
            return framework
    return "vite"


def _collect_files(project_path: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for f in sorted(project_path.rglob("*")):
        if not f.is_file():
            continue
        if any(p in f.parts for p in SKIP_PATHS):
            continue
        rel = str(f.relative_to(project_path))
        if f.stat().st_size > 5 * 1024 * 1024:
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            files[rel] = content
        except Exception:
            pass
    return files


# ── Main deploy function ──────────────────────────────────────────────────────

async def deploy_to_vercel(
    project_id: str,
    project_path: Path,
    project_name: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Deploy a TypeScript project to Vercel.
    Yields SSE-formatted strings for real-time streaming.

    SSE event shapes:
      {"type": "start",    "message": "..."}
      {"type": "log",      "message": "...", "level": "info"|"error"|"warning"}
      {"type": "build",    "message": "...", "step": "..."}
      {"type": "url",      "url": "https://...", "project_id": "..."}
      {"type": "error",    "message": "..."}
      {"type": "done",     "url": "https://...", "deployment_id": "..."}
    """
    import aiohttp

    def evt(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    # SSE comment line — keeps the connection alive through proxies / Railway
    KEEPALIVE = ": ping\n\n"

    try:
        token = _token()
    except RuntimeError as e:
        yield evt({"type": "error", "message": str(e)})
        return

    # ── Step 1: Collect files ─────────────────────────────────────────────────
    yield evt({"type": "start", "message": "📦 جمع ملفات المشروع..."})

    files = _collect_files(project_path)
    if not files:
        yield evt({"type": "error", "message": "No deployable files found in project"})
        return

    framework = _detect_framework(files)
    name = (project_name or f"veylor-{project_id}").lower().replace("_", "-")[:50]

    yield evt({"type": "log", "message": f"🔍 Framework: {framework} | Files: {len(files)}", "level": "info"})

    # ── Step 2: Build Vercel deployment payload ───────────────────────────────
    yield evt({"type": "build", "message": "🚀 إنشاء الـ Deployment على Vercel...", "step": "upload"})

    vercel_files = [
        {
            "file": path,
            "data": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "encoding": "base64",
        }
        for path, content in files.items()
    ]

    if "vercel.json" not in files and framework == "vite":
        vercel_files.append({
            "file": "vercel.json",
            "data": base64.b64encode(
                json.dumps({"buildCommand": "npm run build", "outputDirectory": "dist"}).encode()
            ).decode("ascii"),
            "encoding": "base64",
        })

    payload: dict = {
        "name": name,
        "files": vercel_files,
        "projectSettings": {"framework": framework},
        "target": "production",
    }

    team_id = os.getenv("VERCEL_TEAM_ID") or os.getenv("VERCEL_ORG_ID")
    params = f"?teamId={team_id}" if team_id else ""

    # ── Step 3: Create deployment ─────────────────────────────────────────────
    deployment_id = ""
    deployment_url = ""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_API}/v13/deployments{params}",
                headers=_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    yield evt({"type": "error", "message": f"Vercel API error {resp.status}: {body[:300]}"})
                    return

                data = await resp.json()
                deployment_id = data.get("id", "")
                deployment_url = data.get("url", "")
                alias_url = f"https://{deployment_url}" if deployment_url else ""

                yield evt({
                    "type": "log",
                    "message": f"✅ Deployment created: {deployment_id}",
                    "level": "info",
                })
                if alias_url:
                    yield evt({"type": "url", "url": alias_url, "deployment_id": deployment_id})

            # ── Step 4: Stream build events ───────────────────────────────────
            yield evt({"type": "build", "message": "⚙️ جارٍ البناء (Build)...", "step": "building"})

            events_url = f"{_API}/v1/deployments/{deployment_id}/events{params}"
            headers_no_ct = {"Authorization": f"Bearer {token}"}

            seen_ids: set[str] = set()
            deadline = time.time() + 300   # 5 min max
            last_ping = time.time()
            last_event_count = 0

            while time.time() < deadline:
                # ── Keepalive ping every 10 seconds to prevent Railway timeout ──
                if time.time() - last_ping >= 10:
                    yield KEEPALIVE
                    last_ping = time.time()

                try:
                    async with session.get(
                        events_url,
                        headers=headers_no_ct,
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as evresp:
                        if evresp.status != 200:
                            await asyncio.sleep(3)
                            continue

                        events_data = await evresp.json()
                        events_list = events_data if isinstance(events_data, list) else []

                        ready = False
                        new_events = 0

                        for event in events_list:
                            eid = event.get("id", "")
                            if eid and eid in seen_ids:
                                continue
                            if eid:
                                seen_ids.add(eid)

                            new_events += 1
                            etype = event.get("type", "")
                            payload_data = event.get("payload", {})
                            text = (
                                payload_data.get("text")
                                or payload_data.get("name")
                                or str(payload_data)[:200]
                            )

                            if etype == "stdout":
                                yield evt({"type": "log", "message": text, "level": "info"})
                            elif etype == "stderr":
                                yield evt({"type": "log", "message": text, "level": "warning"})
                            elif etype in ("build-log", "command"):
                                yield evt({"type": "build", "message": text, "step": etype})
                            elif etype == "ready":
                                ready = True
                                yield evt({"type": "build", "message": "✅ Build اكتمل!", "step": "ready"})
                            elif etype == "error":
                                yield evt({"type": "log", "message": f"❌ {text}", "level": "error"})

                        if ready:
                            break

                        # Poll deployment status if no events
                        if not events_list or new_events == 0:
                            async with session.get(
                                f"{_API}/v6/deployments/{deployment_id}{params}",
                                headers=headers_no_ct,
                                timeout=aiohttp.ClientTimeout(total=10),
                            ) as status_resp:
                                if status_resp.status == 200:
                                    status_data = await status_resp.json()
                                    state = status_data.get("readyState", "")
                                    if state == "READY":
                                        final_url = status_data.get("url", deployment_url)
                                        deployment_url = final_url
                                        break
                                    elif state == "ERROR":
                                        yield evt({"type": "error", "message": "البناء فشل على Vercel"})
                                        return

                except Exception as poll_err:
                    yield evt({"type": "log", "message": f"[polling...] {poll_err}", "level": "warning"})

                await asyncio.sleep(3)

    except Exception as exc:
        yield evt({"type": "error", "message": f"Deployment error: {exc}"})
        return

    # ── Step 5: Final result ──────────────────────────────────────────────────
    final_url = f"https://{deployment_url}" if not deployment_url.startswith("http") else deployment_url
    yield evt({
        "type": "done",
        "url": final_url,
        "deployment_id": deployment_id,
        "message": f"🎉 تم النشر بنجاح!\n{final_url}",
    })
