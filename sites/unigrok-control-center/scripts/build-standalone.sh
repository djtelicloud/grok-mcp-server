#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

secret_names=(
  GITHUB_APP_PRIVATE_KEY
  GITHUB_APP_CLIENT_SECRET
  AUTH_SESSION_SECRET
)

for secret_name in "${secret_names[@]}"; do
  if [[ -n "${!secret_name:-}" ]]; then
    echo "Refusing a standalone build while ${secret_name} is present." >&2
    echo "Runtime secrets must be injected by Cloud Run from Secret Manager." >&2
    exit 78
  fi
done

next_bin="${project_root}/node_modules/.bin/next"
if [[ ! -x "${next_bin}" ]]; then
  echo "Next.js is unavailable. Install the locked dependencies before building." >&2
  exit 69
fi

export NEXT_TELEMETRY_DISABLED=1
export UNIGROK_BUILD_TARGET=standalone

"${next_bin}" build "${project_root}"
node "${project_root}/scripts/validate-standalone.mjs"
