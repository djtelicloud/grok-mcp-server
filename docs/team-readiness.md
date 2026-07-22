# Team readiness and handoff

This is the shared evidence path for contributors and release operators. It does not
claim that a checkout, image, public branch, or hosted revision is current; each claim
must be proved independently.

## Four separate truths

| Question | Authority | What is not enough |
| --- | --- | --- |
| What code are we editing? | Current folder contents plus `git status` | A PR page or running container |
| What code is local Docker running? | Runtime `source_fingerprint` plus `scripts/check_runtime_parity.py` | Image tag, container age, or Docker “healthy” |
| What is public source? | Protected public `main` commit | Local branch or unpushed commit |
| What serves remote users? | Public health/readiness, revision header, image digest, authenticated MCP smoke | GitHub merge or control-plane deployment status alone |

Never collapse these into one “deployed” status.

## Source candidate gate

Run from the repository root:

```bash
uv sync --frozen
uv run ruff check .
bash scripts/ci-insider-denylist.sh
uv run python scripts/check_release_contract.py
uv run python scripts/check_docs.py
uv run pytest -q
docker compose config --quiet
```

The candidate is source-ready only when all seven pass and the intended diff has been
reviewed. A passing source gate does not modify Docker or deploy Cloud Run.

## Local Docker gate

Compose copies `src/` into the image. It has no source bind mount, so editing or
committing the folder never changes the running service. Every local IDE configured for
port `4765` reaches the same container.

Before any rebuild or recreation:

1. Check `/runtimez` for runtime controls, breaker state, the current
   `source_fingerprint`, and both worker counters. Drain requires
   `grok_build.in_flight=0` and `durable_jobs.active=0`.
2. Record the current image ID and identify the rollback image.
3. Drain or explicitly accept interruption of active jobs. A generic provider operation
   interrupted before its outcome is persisted becomes `lost`.
4. Preserve the `unigrok-cli-auth` and `unigrok-public-state` volumes unless data loss is
   an explicit decision.

Prove checkout/container parity without copying or changing runtime files:

```bash
uv run python scripts/check_runtime_parity.py --container unigrok
```

`MATCH` proves byte identity for the public runtime tree. `DRIFT` is a stop signal: decide
which source is authoritative before rebuilding. The command never changes the
container.

After an intentional rebuild, require:

```bash
curl -fsS http://127.0.0.1:4765/healthz
curl -fsS http://127.0.0.1:4765/readyz
curl -fsS http://127.0.0.1:4765/runtimez
uv run python scripts/smoke_mcp.py --url http://127.0.0.1:4765/mcp
uv run python scripts/check_runtime_parity.py --container unigrok
```

`/healthz` proves process liveness. `/readyz` additionally requires SQLite plus a usable
credential plane. Docker's health label alone is not application readiness.

## Local application and state rollback

Application rollback and SQLite rollback are separate decisions. Before a candidate
touches the persistent volume, stop the drained service, record its image ID, give that
image an explicit rollback tag, and copy the stopped state directory into the ignored,
Finder-visible `local-backups/` folder. Include the database plus any `-wal` and `-shm`
files as one set:

```bash
docker compose stop grok-mcp
docker inspect unigrok --format '{{.Image}}'
docker image tag <recorded-image-id> unigrok:rollback-before-candidate
mkdir -p local-backups/before-candidate/state
docker cp unigrok:/state/. local-backups/before-candidate/state/
```

Replace `<recorded-image-id>` with the exact value printed by the preceding command.
The Compose image is selectable without retagging the release default:

```bash
UNIGROK_IMAGE=unigrok:team-ready-candidate UNIGROK_PORT=4775 \
  docker compose --env-file .env up --build -d grok-mcp
```

Roll back the application first while retaining current state:

```bash
docker compose stop grok-mcp
UNIGROK_IMAGE=unigrok:rollback-before-candidate UNIGROK_PORT=4765 \
  docker compose --env-file .env up --no-build --force-recreate -d grok-mcp
```

Then repeat health, readiness, runtime, MCP smoke, and source-fingerprint checks. Restore
the SQLite snapshot only if the old application cannot safely use the post-candidate
schema or the candidate damaged state. State restoration discards newer sessions, jobs,
and receipts, so stop the service and retain a second copy first:

```bash
docker compose stop grok-mcp
mkdir -p local-backups/post-candidate/state
docker cp unigrok:/state/. local-backups/post-candidate/state/
docker run --rm --user 1000:1000 \
  --mount type=volume,src=unigrok-public-state,dst=/state \
  --mount type=bind,src="$PWD/local-backups/before-candidate/state",dst=/backup,readonly \
  --mount type=bind,src="$PWD/scripts/restore_local_state.py",dst=/restore.py,readonly \
  --entrypoint /app/.venv/bin/python unigrok:rollback-before-candidate \
  /restore.py --state-dir /state --backup-dir /backup \
  --confirm stopped-service-state-rollback
```

The helper validates the backup, restores the database and sidecars as one set, and
keeps the displaced state in a timestamped directory inside the volume. Start the
rollback image only after it reports `OK`. Never restore or replace the OAuth volume as
part of an application rollback.

## Durable-job and Mission V2 acceptance

| Observation | Meaning | Operator action |
| --- | --- | --- |
| `pending` | Worker outlived the sync window | Poll `agent_result` with the same `job_id`; do not duplicate work |
| Generic `complete` or `error` | Terminal result is persisted | Consume the stored payload |
| Generic `lost` | Service stopped before a provider outcome was recorded | Inspect provider state before retrying metered or mutating work |
| Mission `continue` | Durable mission is waiting or has repairable gaps | Reattach serially with the same `continue_token` |
| Mission terminal | Canonical mission truth is sealed | Reattach is read-only; no model rerun |
| Repeated `cas_verifying_failed` | Fenced commit did not advance | Stop blind polling, capture the mission receipt, and escalate |

Do not fire concurrent reattach calls for one token. Do not change the Mission V2
envelope version or disable Mission V2 while missions are active unless degraded recovery
is an explicit team decision.

The pre-release reliability suite must cover: pending-to-terminal polling, explicit
cancel, shutdown during a generic mutation, completion/shutdown races, restart polling,
rapid serial Mission V2 reattach, and final `committed=true`.

## Circuit-breaker incident matrix

| Runtime state | Interpretation | Action |
| --- | --- | --- |
| `open=false`, `half_open=false` | Closed | Normal operation |
| `open=true`, retry time positive | Cooldown | Wait; investigate provider failures |
| `open=true`, retry time zero | Probe-ready | Allow the runtime's single probe; do not stampede it manually |
| `half_open=true` | One probe owns admission | Concurrent calls should fail fast |

Cancellation does not count as a provider failure, but a cancelled half-open probe is
fenced through the longest configured provider deadline. Local budget, hard-cap, and
capability-policy refusals must not poison provider health. Prefer observation over a
restart: restarting to clear a breaker can turn active work into `lost`.

## Hosted release evidence

Use the full [remote deployment runbook](remote-mcp-deployment.md). A public-safe handoff
contains:

```text
Source commit:
Immutable image digest:
Candidate active / standby revisions:
Previous known-good revision:
Non-secret effective runtime settings:
Source tests / lint / boundary gate:
Candidate readiness + authenticated MCP smoke:
Public hostname smoke and X-UniGrok-Revision:
Cutover time:
Observation-window end:
Errors, 5xx, breaker trips, lost jobs:
Rollback decision and owner:
```

Filled records containing infrastructure identifiers, OAuth principals, secret versions,
credentials, job tokens, or private research stay in the operator-private system. Public
Core receives only scrubbed product behavior and reproducible tests.

## Handoff vocabulary

- **Ready:** candidate gates pass; not yet serving users.
- **Live:** authoritative endpoint proves the intended revision and smoke contract.
- **Blocked:** one named gate failed with evidence.
- **Rolled back:** traffic returned to the recorded known-good revision and public smoke
  passed.
- **Monitor-only:** healthy state with no evidence-backed mutation to make.

Architecture is documented in [Public architecture](architecture.md). Known limitations
remain in [Known limits](known-limits.md).
