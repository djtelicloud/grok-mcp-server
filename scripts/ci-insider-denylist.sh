#!/usr/bin/env bash
# Insider-boundary deny-list (CI gate).
#
# The public gateway image must contain only the generic clone/build/start/use
# experience documented in README.md. Private UI, topology, coordination, raw
# evaluation, operator state, and secret material stay outside this repository.
# This checks tracked plus untracked, non-ignored repository files so a local gate
# catches provenance before staging. The deny-list itself is excluded from text scans.
set -euo pipefail

deny_patterns=(
  '(^|/)\.DS_Store$'         # Finder metadata
  '^\.agents($|/)'               # private shared-agent canon and skills
  '^\.claude($|/)'               # provider-local state
  '^\.a2a($|/)'                  # private team relay
  '^agentixos($|/)'              # private control plane and courier runtime
  '^architecture/agentixos($|/)' # private control-plane contracts
  '^archives($|/)'               # private historical material
  '^campaigns($|/)'              # private orchestration campaigns
  '^codex($|/)'                  # private provider workspace
  '^cursor($|/)'                 # private provider workspace
  '^evals($|/)'                  # raw evaluation artifacts
  '^harvest($|/)'                # private derived-work staging
  '^mcp_ui($|/)'                 # private console tree
  '^playbooks($|/)'              # private orchestration methods
  '^providers($|/)'              # private provider profiles and adapters
  '^sites($|/)'                  # private control-center app tree
  '^tools($|/)'                  # private experimental and training suites
  '(^|/)forge-console($|/)'      # private launch tooling
  '^docs/(AUTONOMY_INTEL|DEOVERFIT|OFFLINE_FREE_VARIANT|PUBLIC_STRANGER_SURFACE|SLEEP_PLANE|WASM_DOGFOOD)\.md$'
  '^docs/(design/ui-data-pipeline|github-copilot-mcp|onboarding-extraction|remote-mcp-deployment|team-readiness)\.md$'
  '^scripts/(bench_slim_preprompt|bench_stable_vs_slim|benchmark_deep|dogfood_optimize|parallel_probe|persona_bench|smoke_team_harness|soak_nokey|triage_optimize)\.py$'
  '^scripts/(check_runtime_parity|seed_dev_telemetry)\.py$'
  '^assets/(control-center-live|logo|og-banner)\.(png|svg)$'
  '^src/unigrok_public/github_auth\.py$'
  '^tests/test_(github_device_auth|control_oauth_bridge|forge_surface)\.py$'
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
  '(SkyCommand|SpaceCommand|GroundCommand)'
  '@(sky|space)grok([^A-Za-z0-9_]|$)'
  'UNIGROK_(SKY|SPACE|FORGE|SURFACE|CONTRIBUTOR)'
  'XAI_API_KEY_(SKY_INFERENCE|GROUND|UNIGROK_GROUND)'
  'KEY_HOMES\.md'
  'grok-mcp-intelligence'
  'telemetry([ _-]?(ID|id))?[` )=:#-]*[0-9]{2,}'
  'metered cost of \$[0-9]'
)

print_path() {
  local path="$1"
  if (export LC_ALL=C; [[ "$path" =~ ^[[:print:]]+$ ]]); then
    printf '%s' "$path"
  else
    printf 'hex:'
    printf '%s' "$path" | LC_ALL=C od -An -v -tx1 | tr -d '[:space:]'
  fi
}

fail=0

# Git tree modes are authoritative for tracked symlinks and nested repositories.
# Inspect them before content so a link can never make the scanner follow data
# outside the public clone. NUL delimiters preserve tabs, newlines, and Unicode.
while IFS= read -r -d '' entry; do
  mode="${entry%% *}"
  path="${entry#*$'\t'}"
  case "$mode" in
    100644|100755)
      ;;
    120000)
      printf 'insider-denylist: FORBIDDEN tracked symlink: ' >&2
      print_path "$path" >&2
      printf '\n' >&2
      fail=1
      ;;
    160000)
      printf 'insider-denylist: FORBIDDEN tracked nested repository: ' >&2
      print_path "$path" >&2
      printf '\n' >&2
      fail=1
      ;;
    *)
      printf 'insider-denylist: FORBIDDEN tracked Git mode %s: ' "$mode" >&2
      print_path "$path" >&2
      printf '\n' >&2
      fail=1
      ;;
  esac
done < <(git ls-files --cached --stage -z)

# Preserve the pre-staging guarantee too: an untracked link is still outside
# the public clone contract even before it has a Git mode.
while IFS= read -r -d '' path; do
  if [ -L "$path" ]; then
    printf 'insider-denylist: FORBIDDEN untracked symlink: ' >&2
    print_path "$path" >&2
    printf '\n' >&2
    fail=1
  fi
done < <(git ls-files --others --exclude-standard -z)

for pattern in "${deny_patterns[@]}"; do
  pattern_failed=0
  while IFS= read -r -d '' path; do
    if [[ "$path" =~ $pattern ]]; then
      if [ "$pattern_failed" -eq 0 ]; then
        printf "insider-denylist: FORBIDDEN repository path(s) matching '%s':\n" \
          "$pattern" >&2
      fi
      printf '  ' >&2
      print_path "$path" >&2
      printf '\n' >&2
      pattern_failed=1
    fi
  done < <(git ls-files --cached --others --exclude-standard -z)
  if [ "$pattern_failed" -ne 0 ]; then
    fail=1
  fi
done

for pattern in "${provenance_patterns[@]}"; do
  pattern_failed=0
  while IFS= read -r -d '' path; do
    [ -f "$path" ] || continue
    [ -L "$path" ] && continue
    [ "$path" = "scripts/ci-insider-denylist.sh" ] && continue
    if matches="$(grep -I -n -E "$pattern" -- "$path")"; then
      if [ "$pattern_failed" -eq 0 ]; then
        printf "insider-denylist: FORBIDDEN private provenance matching '%s':\n" \
          "$pattern" >&2
      fi
      printf '  file=' >&2
      print_path "$path" >&2
      printf '\n%s\n' "$matches" >&2
      pattern_failed=1
    fi
  done < <(git ls-files --cached --others --exclude-standard -z)
  if [ "$pattern_failed" -ne 0 ]; then
    fail=1
  fi
done

# example.env is the one sanctioned env template.
if [ "$fail" -ne 0 ]; then
  echo "insider-denylist: FAILED — insider or secret material must not land in the public repo" >&2
  exit 1
fi
echo "insider-denylist: OK (no insider paths, overlays, or secret files tracked)"
