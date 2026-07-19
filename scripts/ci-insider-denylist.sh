#!/usr/bin/env bash
# Insider-boundary deny-list (CI gate).
#
# The public gateway image must never ship contributor/forge UI assets, private
# deploy overlays, or secret material. Forge deployments mount their private
# console at runtime (UNIGROK_UI_ROOT); nothing insider is ever committed here.
# This checks TRACKED PATHS, so the deny-list itself can be documented freely.
set -euo pipefail

deny_patterns=(
  '^mcp_ui/'                 # private forge console tree
  '^sites/'                  # private control-center app tree
  '(^|/)forge-console'       # private launch tooling
  '\.override\.ya?ml$'       # private compose overlays
  '(^|/)\.env($|\.)'         # environment secret files
  'client_secret'            # OAuth secret material
  '(^|/)id_(rsa|ed25519)'    # private keys
  '\.pem$'
)

fail=0
tracked="$(git ls-files)"
for pattern in "${deny_patterns[@]}"; do
  if hits="$(grep -E "$pattern" <<<"$tracked")"; then
    echo "insider-denylist: FORBIDDEN tracked path(s) matching '$pattern':" >&2
    echo "$hits" >&2
    fail=1
  fi
done

# example.env is the one sanctioned env template.
if [ "$fail" -ne 0 ]; then
  echo "insider-denylist: FAILED — insider or secret material must not land in the public repo" >&2
  exit 1
fi
echo "insider-denylist: OK (no insider paths, overlays, or secret files tracked)"
