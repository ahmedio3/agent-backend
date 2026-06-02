---
name: Veylor tier naming
description: API tier values and model mapping after rename from Vire
---

# Veylor Tier Naming

**Why:** User renamed product from Vire to Veylor and changed tier labels.

## Tier values in API requests (tier field in all endpoints)
- `"x1.0"` — was "mini" — gemini-3.1-flash-lite everywhere
- `"x1.5"` — was "core" — gemini-3.5-flash for planner only, lite for all other agents
- `"x2.0"` — was "max" — gemini-3.5-flash everywhere, uses VEYLOR_MAX_KEYS

## Env vars
- `GEMINI_KEYS` — keys for x1.0 and x1.5
- `VEYLOR_MAX_KEYS` — dedicated keys for x2.0 (was VIRE_MAX_KEYS)

## How to apply
Any code checking `tier == "mini"` etc. must be updated to `tier == "x1.0"`. All agents (orchestrator, planner, coder, debugger, assistant, doc_agent, test_agent) have been updated.
