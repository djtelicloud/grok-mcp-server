# Phase 3 SRP — chats + system + media

**Branch:** `terminalgrok/phase3-srp-chats-system`

## Honest critique of wave 1
- **Too thin:** mostly response-shape shims; server still ~8k lines; no real risk removed.
- **Missed behavior ownership:** validators, status, media params still lived in server.py.
- **Good:** package boundary exists; pure unit tests; registration stayed stable.

## Wave 2 (this commit)
| Domain | Module | Owns now |
|--------|--------|----------|
| system | `tools/system.py` | healthz, readyz, status, list_models, benchmark_status, runtimez core/merge |
| chats | `tools/chats.py` | chat context join, session list/history/forget shapes |
| media | `tools/media.py` | file_id/URL validation, image/video/upload param checks (logic moved out of server) |

## Still deferred
- Moving FastMCP registration out of server.py
- Extracting `_run_unified` / agent hive
- Full media tool handlers (still durable-job wrappers in server)
- server.py under 4k lines

## Verify
```bash
PYTHONPATH=src pytest tests/test_phase3_tools_srp.py -q
```
