# Phase 3 SRP — chats + system + media

**Branch:** `terminalgrok/phase3-srp-chats-system`

## Wave 3 (media plans)
- `tools/media.py` owns **ImageGenPlan / VideoGenPlan / VideoExtendPlan / UploadPlan**
- server tools call `plan_*` then durable-job + `xai_api` (registration stays in server)
- list_files limit clamp in media

## Prior waves
- Wave 1: thin builders (critiqued as too light)
- Wave 2: real validators + system status/list_models/runtimez

## Remaining
- chat_with_vision / chat_with_files prep helpers
- agent/router pure helpers → tools/agent.py
- registration factory (optional)
- server.py still large; continue incremental shrink

## Verify
```bash
PYTHONPATH=src pytest tests/test_phase3_tools_srp.py -q
```
