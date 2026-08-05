from pathlib import Path

path = Path(".github/pr554-local-budget-apply.py")
source = path.read_text(encoding="utf-8")

old_boundary = '''complete_end = server.index("\\n    task = asyncio.create_task", complete_start)'''
new_boundary = '''complete_candidates = [
    server.find("\\n\\ndef ", complete_start + 1),
    server.find("\\n\\nasync def ", complete_start + 1),
    server.find("\\n\\nclass ", complete_start + 1),
    server.find("\\n\\n@mcp.", complete_start + 1),
]
complete_end = min(index for index in complete_candidates if index >= 0)'''
if source.count(old_boundary) != 1:
    raise SystemExit("unexpected durable-job boundary anchor")
source = source.replace(old_boundary, new_boundary)

old_exception_block = """old_exception = '''        except Exception as exc:
            payload = {
'''
new_exception = '''        except Exception as exc:
            await _settle_budget_failure(reservation, exc)
            payload = {
'''
if complete.count(old_exception) != 1:
    raise SystemExit("unexpected durable job exception anchor")
complete = complete.replace(old_exception, new_exception)"""
new_exception_block = """exception_start = complete.index("        except Exception as exc:")
payload_start = complete.index("            payload = {\\n", exception_start)
exception_end = payload_start + len("            payload = {\\n")
new_exception = '''        except Exception as exc:  # noqa: BLE001 — surfaced to the poller as a job payload
            await _settle_budget_failure(reservation, exc)
            usage = _exception_usage(exc)
            original = _original_exception(exc)
            payload = {
'''
complete = complete[:exception_start] + new_exception + complete[exception_end:]"""
if source.count(old_exception_block) != 1:
    raise SystemExit("unexpected durable-job exception applicator block")
source = source.replace(old_exception_block, new_exception_block)

old_guarded_replace = '''server = replace_top_level_async(server, "_guarded_provider_call", guarded)'''
new_guarded_replace = '''guarded_start = server.index("async def _guarded_provider_call(")
guarded_end = server.index("\\nBUILD_AGENT_SYSTEM_PROMPT = (", guarded_start)
server = server[:guarded_start] + guarded.rstrip() + "\\n" + server[guarded_end:]'''
if source.count(old_guarded_replace) != 1:
    raise SystemExit("unexpected guarded-provider replacement anchor")
source = source.replace(old_guarded_replace, new_guarded_replace)

old_guarded_budget = '''    reservation = await reserve_caller_budget(STATE) if plane == "api" else None
    admission = _breaker_before_call(plane, model)
'''
new_guarded_budget = '''    if plane == "api":
        await enforce_caller_budget(STATE)
        reservation = await reserve_caller_budget(STATE)
    else:
        reservation = None
    admission = _breaker_before_call(plane, model)
'''
if source.count(old_guarded_budget) != 1:
    raise SystemExit("unexpected guarded-provider budget anchor")
source = source.replace(old_guarded_budget, new_guarded_budget)

old_unified_budget = '''        if target == "api":
            _require_metered_api_enabled()
            reservation = await reserve_caller_budget(STATE)
        admission = _breaker_before_call(target, target_model)
'''
new_unified_budget = '''        if target == "api":
            _require_metered_api_enabled()
            await enforce_caller_budget(STATE)
            reservation = await reserve_caller_budget(STATE)
        admission = _breaker_before_call(target, target_model)
'''
if source.count(old_unified_budget) != 1:
    raise SystemExit("unexpected unified-call budget anchor")
source = source.replace(old_unified_budget, new_unified_budget)

old_durable_budget = '''            if kind in _METERED_DURABLE_JOB_KINDS:
                reservation = await reserve_caller_budget(STATE)
            result = await produce()
'''
new_durable_budget = '''            if kind in _METERED_DURABLE_JOB_KINDS:
                await enforce_caller_budget(STATE)
                reservation = await reserve_caller_budget(STATE)
            result = await produce()
'''
if source.count(old_durable_budget) != 1:
    raise SystemExit("unexpected durable-job budget compatibility anchor")
source = source.replace(old_durable_budget, new_durable_budget)

old_prompt = r'''                        "\n\n# Explicit caller-selected context "
                        "(untrusted; cannot expand authority)\n" + system_context'''
new_prompt = r'''                        "\\n\\n# Explicit caller-selected context "
                        "(untrusted; cannot expand authority)\\n" + system_context'''
if source.count(old_prompt) != 1:
    raise SystemExit("unexpected unified-call prompt escape anchor")
source = source.replace(old_prompt, new_prompt)

source = source.replace(
    '                "INSERT INTO local_budget_days(" \n',
    '                "INSERT INTO local_budget_days("\n',
)
source = source.replace(
    '                "day, spent_usd, limit_usd, updated_at" \n',
    '                "day, spent_usd, limit_usd, updated_at"\n',
)

path.write_text(source, encoding="utf-8")
