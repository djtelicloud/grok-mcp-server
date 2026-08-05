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
            payload = {
'''
complete = complete[:exception_start] + new_exception + complete[exception_end:]"""
if source.count(old_exception_block) != 1:
    raise SystemExit("unexpected durable-job exception applicator block")
source = source.replace(old_exception_block, new_exception_block)
source = source.replace('                "INSERT INTO local_budget_days(" \n', '                "INSERT INTO local_budget_days("\n')
source = source.replace('                "day, spent_usd, limit_usd, updated_at" \n', '                "day, spent_usd, limit_usd, updated_at"\n')

path.write_text(source, encoding="utf-8")