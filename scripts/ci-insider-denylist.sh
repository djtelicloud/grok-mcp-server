#!/usr/bin/env bash
# Insider-boundary deny-list (CI gate).
#
# The public gateway image must never ship contributor/forge UI assets, private
# deploy overlays, or secret material. Forge deployments mount their private
# console at runtime (UNIGROK_UI_ROOT); nothing insider is ever committed here.
# This checks tracked plus untracked, non-ignored repository files so a local gate
# catches provenance before staging. The deny-list itself is excluded from text scans.
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

# Public prose may explain the boundary, but it must not preserve private review
# receipts or name internal donor artifacts. Keep these narrow so ordinary uses of
# words such as "private" and the public telemetry_id field remain valid.
provenance_patterns=(
  'internal donor'
  'donor-research'
  'private DoR'
  'telemetry([ _-]?(ID|id))?[` )=:#-]*[0-9]{2,}'
  'metered cost of \$[0-9]'
)

fail=0
repository_files="$(git ls-files --cached --others --exclude-standard)"
for pattern in "${deny_patterns[@]}"; do
  if hits="$(grep -E "$pattern" <<<"$repository_files")"; then
    echo "insider-denylist: FORBIDDEN repository path(s) matching '$pattern':" >&2
    echo "$hits" >&2
    fail=1
  fi
done

for pattern in "${provenance_patterns[@]}"; do
  hits="$({
    while IFS= read -r file; do
      [ -f "$file" ] || continue
      [ "$file" = "scripts/ci-insider-denylist.sh" ] && continue
      grep -I -H -n -E "$pattern" -- "$file" || true
    done <<<"$repository_files"
  } || true)"
  if [ -n "$hits" ]; then
    echo "insider-denylist: FORBIDDEN private provenance matching '$pattern':" >&2
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
