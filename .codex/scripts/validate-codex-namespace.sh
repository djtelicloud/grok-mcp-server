#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CODEX_DIR="$ROOT/.codex"

if command -v python3 >/dev/null 2>&1; then
  PY=(python3)
elif command -v python >/dev/null 2>&1; then
  PY=(python)
elif command -v uv >/dev/null 2>&1; then
  export UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/uv-cache}"
  PY=(uv run python)
else
  echo "No Python runtime found for .codex validation" >&2
  exit 1
fi

"${PY[@]}" - "$CODEX_DIR" <<'PY'
import json
import re
import sys
from pathlib import Path

codex_dir = Path(sys.argv[1])
errors = []

required_files = [
    "CODEX.md",
    "manifest.json",
    "intelligence/codex-intelligence.json",
    "threads/registry.json",
    "handoff/schema.json",
    "directives.md",
    "browser/node-repl.md",
    "computer-use.md",
    "chronicle/triggers.json",
    "memory/context.md",
    "mcp/grok-routing.json",
    "openai-platform/api-key-flow.md",
    "plugins/capabilities.json",
    "security/secret-scan.md",
    "hooks/hooks.json",
    "hooks/README.md",
]

for rel in required_files:
    if not (codex_dir / rel).exists():
        errors.append(f"missing required file: {rel}")

json_files = sorted(codex_dir.rglob("*.json"))
json_data = {}
for path in json_files:
    rel = path.relative_to(codex_dir).as_posix()
    try:
        json_data[rel] = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"invalid json {rel}: {exc}")

def require(rel, keys):
    data = json_data.get(rel)
    if data is None:
        return
    for key in keys:
        if key not in data:
            errors.append(f"{rel} missing key: {key}")

require("manifest.json", ["schema_version", "namespace", "packaged_plugin", "codex_app_functions"])
require("intelligence/codex-intelligence.json", ["schema_version", "task_routes"])
require("threads/registry.json", ["schema_version", "archetypes"])
require("mcp/grok-routing.json", ["schema_version", "server", "routes", "safety"])
require("plugins/capabilities.json", ["schema_version", "plugins"])
require("chronicle/triggers.json", ["schema_version", "triggers"])
require("hooks/hooks.json", ["schema_version", "documentation_only", "hooks"])

manifest = json_data.get("manifest.json", {})
if manifest.get("namespace") != ".codex":
    errors.append("manifest namespace must be .codex")
if manifest.get("packaged_plugin") is not False:
    errors.append("manifest packaged_plugin must be false")

if (codex_dir / ".codex-plugin").exists():
    errors.append(".codex/.codex-plugin must not exist")

registry = json_data.get("threads/registry.json", {})
for item in registry.get("archetypes", []):
    prompt_ref = item.get("initial_prompt_ref")
    if prompt_ref:
        target = (codex_dir / "threads" / prompt_ref).resolve()
        if not target.exists():
            errors.append(f"thread archetype {item.get('id')} missing prompt_ref target: {prompt_ref}")

for rel, data in json_data.items():
    if rel.startswith("automations/"):
        template = data.get("automation_update_template", {})
        mode = template.get("mode")
        if mode not in {"suggested_create", "suggested_update", "create", "update"}:
            errors.append(f"{rel} has unsupported automation mode: {mode}")
        if data.get("codex_app_function") != "automation_update":
            errors.append(f"{rel} must target automation_update")
        if "prompt_ref" in template:
            target = (codex_dir / "automations" / template["prompt_ref"]).resolve()
            if not target.exists():
                errors.append(f"{rel} prompt_ref target missing: {template['prompt_ref']}")

secret_patterns = [
    re.compile(r"XAI_API_KEY\s*="),
    re.compile(r"OPENAI_API_KEY\s*="),
    re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]{10,}", re.I),
    re.compile(r"\bsk-(?:proj|live|test|svcacct)-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxai-[A-Za-z0-9_-]{8,}"),
]
for path in sorted(codex_dir.rglob("*")):
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in secret_patterns:
            if pat.search(text):
                errors.append(f"possible secret pattern in {path.relative_to(codex_dir).as_posix()}: {pat.pattern}")

if errors:
    print("Codex namespace validation failed:", file=sys.stderr)
    for err in errors:
        print(f"- {err}", file=sys.stderr)
    sys.exit(1)

print(f"Validated {len(json_files)} JSON files under {codex_dir}")
PY
