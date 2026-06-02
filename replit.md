# Vire — AI Agent Backend

An AI-powered backend that builds Telegram bots, FastAPI apps, React web apps, and Expo mobile apps on demand, with real-time SSE streaming.

## Run & Operate

- `python main.py` — start the FastAPI server (port 8000)
- Required env: `GEMINI_KEYS` — comma-separated Gemini API keys

## Stack

- Python 3.12, FastAPI, uvicorn
- Gemini API (google-genai) with key rotation and rate limiting
- SSE streaming for real-time agent events
- pnpm workspaces (Node.js scaffold, separate from Python app)

## Where things live

- `main.py` — FastAPI entry point, all HTTP routes
- `agents/` — orchestrator, planner, coder, debugger, assistant agents
- `core/` — Gemini client, event helpers, project store, rate limiter
- `tools/` — command execution, terminal sessions, Vercel deploy

## Architecture decisions

- Three model tiers: mini (gemini-3.1-flash-lite), core (gemini-3.5-flash for planning only), max (gemini-3.5-flash all steps)
- Each agent run creates an isolated `/tmp/agent_<id>` directory
- Project store is in-memory; terminal sessions are tied to project IDs
- SSE streaming yields `data: {...}\n\n` lines from `/agent` and terminal endpoints

## Product

- `POST /analyze` — preview plan + required inputs before running
- `POST /agent` — SSE streaming agent run (builds full projects)
- `POST /terminal` + `/terminal/{id}/exec` — sandboxed terminal sessions
- `GET /project/{id}/download` — download generated project as ZIP
- `GET /usage` — per-key Gemini usage stats

## User preferences

_Populate as you build._

## Gotchas

- `GEMINI_KEYS` must be set — the server will fail to start without at least one key when first used
- Rate limiter tracks per-key RPM/RPD in memory; resets on server restart
- Expo tunnel (`start_expo_tunnel`) only works where Node.js + npx are available

## Pointers

- See the `pnpm-workspace` skill for Node.js workspace structure details
