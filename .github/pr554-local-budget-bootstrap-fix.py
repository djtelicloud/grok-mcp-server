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

old_exception = """old_exception = '''        except Exception as exc:
            payload = {
'''"""
new_exception = """old_exception = '''        except Exception as exc:  # noqa: BLE001 — surfaced to the poller as a job payload
            payload = {
'''"""
if source.count(old_exception) != 1:
    raise SystemExit("unexpected durable-job exception literal")
source = source.replace(old_exception, new_exception)

path.write_text(source, encoding="utf-8")
