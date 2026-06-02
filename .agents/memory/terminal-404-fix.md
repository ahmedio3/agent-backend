---
name: Terminal 404 fix
description: Why terminal sessions return 404 after server restart and how it's fixed
---

# Terminal 404 Fix

**Why:** project_store is in-memory. After Railway restarts the server, all project_ids are gone → POST /terminal returns 404.

## Fix implemented
`_resolve_project_path(project_id)` in `main.py`:
1. First checks `project_store.get(project_id)` (in-memory)
2. If not found, checks `/tmp/agent_{project_id}` on disk
3. If found on disk, calls `project_store.register()` to re-register it
4. Returns None only if neither exists

Used in: create_terminal, terminal_ai, project files/download, Vercel and GitHub deploy endpoints.

## How to apply
Always use `_resolve_project_path()` instead of `project_store.get()` in any endpoint that takes a project_id.
