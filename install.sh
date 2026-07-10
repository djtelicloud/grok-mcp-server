#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

UV_BIN="${UV_BIN:-uv}"
if ! command -v "$UV_BIN" >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    UV_BIN="$HOME/.local/bin/uv"
  else
    echo "Missing dependency: uv. Install it from https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
  fi
fi

echo "Checking local dependencies..."
for bin in git docker; do
  if command -v "$bin" >/dev/null 2>&1; then
    echo "  $bin: found"
  else
    echo "  $bin: not found"
  fi
done
echo "  uv: $UV_BIN"

echo
echo "Syncing Python environment..."
"$UV_BIN" sync

echo
echo "Running UniGrok init..."
"$UV_BIN" run python main.py init

echo
if command -v docker >/dev/null 2>&1; then
  echo "Validating Docker Compose configuration..."
  docker compose config >/dev/null
  echo "Compose config OK."
else
  echo "Docker not found; skipping Compose validation."
fi

echo
echo "Next steps:"
echo "  1. Edit .env and set XAI_API_KEY."
echo "  2. Start the shared MCP service: docker compose up --build -d"
echo "  3. Check health: curl -s http://localhost:4765/healthz"
