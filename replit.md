# Veylor — AI Agent Backend

An AI-powered backend that builds Telegram bots, FastAPI apps, React web apps, and Expo mobile apps on demand, with real-time SSE streaming.

## Run & Operate

- `python main.py` — start the FastAPI server (port 8000)
- Required env: `GEMINI_KEYS` — comma-separated Gemini API keys
- Optional env: `VEYLOR_MAX_KEYS` — dedicated keys for Veylor x2.0 tier

## Stack

- Python 3.12, FastAPI, uvicorn
- Gemini API (google-genai) with key rotation and rate limiting
- SSE streaming for real-time agent events
- pnpm workspaces (Node.js scaffold, separate from Python app)

## Where things live

- `main.py` — FastAPI entry point, all HTTP routes
- `agents/` — orchestrator, planner, coder, debugger, assistant agents
- `core/` — Gemini client, event helpers, project store, rate limiter
- `tools/` — command execution, terminal sessions, Vercel deploy, GitHub push

## Architecture decisions

- Three model tiers:
  - **Veylor x1.0** — gemini-3.1-flash-lite everywhere
  - **Veylor x1.5** — gemini-3.5-flash for planning only, gemini-3.1-flash-lite for all other agents (5 RPM / 20 RPD limit)
  - **Veylor x2.0** — gemini-3.5-flash everywhere, per-key rate limiting (VEYLOR_MAX_KEYS)
- Each agent run creates an isolated `/tmp/agent_<id>` directory
- Project store is in-memory; `_resolve_project_path()` auto-discovers `/tmp/agent_{id}` after server restart (fixes terminal 404)
- SSE streaming yields `data: {...}\n\n` lines from `/agent`, terminal, deploy endpoints
- Vercel SSE sends `: ping\n\n` keepalive every 10 seconds to prevent Railway timeout

## Product

- `POST /analyze` — preview plan + required inputs before running
- `POST /agent` — SSE streaming agent run (tier = x1.0|x1.5|x2.0)
- `POST /terminal` + `/terminal/{id}/exec` — sandboxed terminal sessions
- `GET /project/{id}/download` — download generated project as ZIP
- `GET /usage` — per-key Gemini usage stats
- `POST /deploy/vercel/{id}` — deploy React/TS project to Vercel (SSE, requires VERCEL_TOKEN)
- `POST /deploy/github/{id}` — push project to GitHub (SSE, uses per-request PAT token)

## User preferences

_Populate as you build._

## Gotchas

- `GEMINI_KEYS` must be set — the server will fail to start without at least one key when first used
- Rate limiter tracks per-key RPM/RPD in memory; resets on server restart
- Expo tunnel (`start_expo_tunnel`) only works where Node.js + npx are available
- `VEYLOR_MAX_KEYS` replaces the old `VIRE_MAX_KEYS` env var for x2.0 dedicated keys

## Pointers

- See the `pnpm-workspace` skill for Node.js workspace structure details
