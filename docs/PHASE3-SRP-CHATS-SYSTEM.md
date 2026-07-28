# Phase 3 SRP — chats + system (bounded)

**Branch:** `terminalgrok/phase3-srp-chats-system`  
**Goal:** Decompose the monolithic `server.py` into domain modules without behavior drift.

## Domains (this wave)

| Domain | Module | Owns |
|--------|--------|------|
| **system** | `src/unigrok_public/tools/system.py` | `/healthz`, `/readyz` payload builders; `/runtimez` core identity fields |
| **chats** | `src/unigrok_public/tools/chats.py` | `chat` system-context join; `list_sessions` / `session_history` / `forget_session` response shape |

FastMCP `@mcp.tool` / `@mcp.custom_route` registration **stays in server.py** for this wave (stable discovery). Logic moves out first.

## Non-goals (this wave)
- Full media domain extraction
- Moving `_run_unified` / agent hive
- JSON chat-file migration (already SQLite on main path)
- Public promote / release cut

## Next waves
1. `tools/media.py` — image/video/file tools  
2. Extract `runtimez` remainder + status tools into system  
3. Optional FastMCP registration helpers per domain  
4. Shrink server.py under 4k lines

## Verify
```bash
pytest tests/test_phase3_tools_srp.py -q
pytest tests/test_public_boundary.py -q --tb=no
```
