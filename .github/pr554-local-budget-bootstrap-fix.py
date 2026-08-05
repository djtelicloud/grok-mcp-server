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

old_guarded_budget = '''    reservation = await reserve_caller_budget(STATE) if plane == "api" else None'''
new_guarded_budget = '''    reservation = None
    if plane == "api":
        await enforce_caller_budget(STATE)
        reservation = await reserve_caller_budget(STATE)'''
if source.count(old_guarded_budget) != 1:
    raise SystemExit("unexpected guarded-provider budget anchor")
source = source.replace(old_guarded_budget, new_guarded_budget)

old_unified_budget = '''        if target == "api":
            _require_metered_api_enabled()
            reservation = await reserve_caller_budget(STATE)'''
new_unified_budget = '''        if target == "api":
            _require_metered_api_enabled()
            await enforce_caller_budget(STATE)
            reservation = await reserve_caller_budget(STATE)'''
if source.count(old_unified_budget) != 1:
    raise SystemExit("unexpected unified-call budget anchor")
source = source.replace(old_unified_budget, new_unified_budget)

old_durable_budget = '''            if kind in _METERED_DURABLE_JOB_KINDS:
                reservation = await reserve_caller_budget(STATE)'''
new_durable_budget = '''            if kind in _METERED_DURABLE_JOB_KINDS:
                await enforce_caller_budget(STATE)
                reservation = await reserve_caller_budget(STATE)'''
if source.count(old_durable_budget) != 1:
    raise SystemExit("unexpected durable-job budget anchor")
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

compatibility_postlude = r'''

server_path = Path("src/unigrok_public/server.py")
server = server_path.read_text(encoding="utf-8")
import_block = '''from .caller_budget import (
    CallerBudgetReservation,
    enforce_caller_budget,
    reserve_caller_budget,
    validate_caller_budget_configuration,
)'''
if import_block not in server:
    fallback_block = '''from .caller_budget import (
    CallerBudgetReservation,
    reserve_caller_budget,
    validate_caller_budget_configuration,
)'''
    if fallback_block not in server:
        raise SystemExit("generated server caller-budget import is unexpected")
    server = server.replace(fallback_block, import_block)

guarded_anchor = '''    if plane == "api":
        reservation = await reserve_caller_budget(STATE)'''
guarded_replacement = '''    if plane == "api":
        await enforce_caller_budget(STATE)
        reservation = await reserve_caller_budget(STATE)'''
if guarded_anchor in server:
    server = server.replace(guarded_anchor, guarded_replacement, 1)

unified_anchor = '''        if target == "api":
            _require_metered_api_enabled()
            reservation = await reserve_caller_budget(STATE)'''
unified_replacement = '''        if target == "api":
            _require_metered_api_enabled()
            await enforce_caller_budget(STATE)
            reservation = await reserve_caller_budget(STATE)'''
if unified_anchor in server:
    server = server.replace(unified_anchor, unified_replacement, 1)

durable_anchor = '''            if kind in _METERED_DURABLE_JOB_KINDS:
                reservation = await reserve_caller_budget(STATE)'''
durable_replacement = '''            if kind in _METERED_DURABLE_JOB_KINDS:
                await enforce_caller_budget(STATE)
                reservation = await reserve_caller_budget(STATE)'''
if durable_anchor in server:
    server = server.replace(durable_anchor, durable_replacement, 1)

if server.count("await enforce_caller_budget(STATE)") < 3:
    raise SystemExit("generated server did not preserve caller-budget compatibility calls")
server_path.write_text(server, encoding="utf-8")
'''

source += compatibility_postlude
path.write_text(source, encoding="utf-8")
