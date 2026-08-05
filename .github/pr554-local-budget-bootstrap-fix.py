from pathlib import Path

path = Path(".github/pr554-local-budget-apply.py")
source = path.read_text(encoding="utf-8")
old = '''complete_end = server.index("\\n    task = asyncio.create_task", complete_start)'''
new = '''complete_candidates = [
    server.find("\\n\\ndef ", complete_start + 1),
    server.find("\\n\\nasync def ", complete_start + 1),
    server.find("\\n\\nclass ", complete_start + 1),
    server.find("\\n\\n@mcp.", complete_start + 1),
]
complete_end = min(index for index in complete_candidates if index >= 0)'''
if source.count(old) != 1:
    raise SystemExit("unexpected durable-job boundary anchor")
path.write_text(source.replace(old, new), encoding="utf-8")
