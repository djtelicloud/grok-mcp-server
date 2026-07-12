# Codex Active Work

Last updated: 2026-07-12
Owner: Codex
Status: Insider intelligence payload protocols landed and public OKF deployed; no active gate

This is the project-scoped handoff for new Codex chats. Resolve drift-prone
Git, CI, runtime, DNS, and cloud identifiers live before acting. Never record
credentials, OAuth codes, tokens, or private keys here.

## Completed repository state

- PR #44 merged to protected `origin/main` as
  `18798c0cb0b85a22e9e15d5cbd6e300999c1a737`. The exact reviewed,
  CI-green, Codex-approved, and locally landed head was
  `a966ef8c138c6864242c862793aa35c714608339`.
- PR #46 merged the stale-artifact deployment guard to protected `origin/main`
  as `8965a46da70340e37ea3d3e7d52f1d35a709bf1b`. Its exact reviewed,
  CI-green, Codex-approved, and locally landed head was
  `5c03076a7d20ff7b7fd60d89060c1cbd9eba7abb`.
- IntelligenceCapsule v1 remains byte-for-byte unchanged. Its schema SHA-256
  is `10c2ec4638bd6c4e303b3e2c4c7d91ae582554f48aaa01fac2d9370062b98d4c`
  and its deterministic SHA-1-format Git genesis remains
  `6dadda28ac4174bf227f36b45917e15c663987ce`.
- The existing `body.payload = {schema,data}` seam now has three separately
  versioned Insider profiles:
  - GNO envelope: `4c7fb150b3f82738ae43d52669c8c663283807d42add1f4532f01527a4d70665`
  - OptiBench result: `dfc216d1855eb36e54829c3aca00434f0dc9845a6efc205c2c49016531accf81`
  - Agentic DPO pair: `7db601ccc11aaa94409383f88c7305a46b63a705176897aaa313f835b24bed84`
- The separate Needle tools-context projection schema is pinned to
  `ac92a88b87e35254a7eef4a151d8743418ef102402022b228609743cbcbf7496`.
  It carries verified examples through Needle's real nested tools-JSON input;
  it is inference-time context, not parameter training or an authority gate.
- Normative evidence/graph algorithms are pinned by semantic contract
  `7464c2343c3edaadc21a14a880e689ef8e4b4ac0fa3fc07b2b6f37b08733545a`
  and public conformance vectors
  `6a0df82c82cd3bfbadc6ff1febf1e43b2d2a6446acd1b977ae7d3262de8d98f4`.
- Python is the executable evidence/complete-graph promotion verifier;
  TypeScript performs matching structural profile validation. Generic Capsule
  validation remains an independent gate.
- GNO receipts are manifest-closed and role-bound. OptiBench recomputes raw
  medians, shared baselines, final Pareto ranks, and exact rational crowding
  over a closed population. DPO requires registered GNO task/candidate parents,
  complete acyclic closure, exact task/output semantic roles, and a digest-bound
  mapping for every cohort benchmark before JSONL or Needle projection.
- Public MCP consumer SQLite is unchanged. No profile opens, migrates, exports,
  or synchronizes `grok_sessions.db`; the Insider DAG remains a separate layer.
- The source distribution now excludes local site dependencies and caches, and
  CI rejects an sdist above 10 MiB, 2,000 entries, or containing forbidden
  dependency/build trees.

## Verification receipt

- The repository landing gate printed
  `LANDED TO MAIN: a966ef8c138c6864242c862793aa35c714608339` after 1,151 tests
  and a rebuilt, smoke-tested contributor container.
- Python 3.11/3.12 CI, Project Site, standalone control image, Docker health,
  offline evals, CodeQL, and exact-head `Codex Approval` all passed on that
  head. Local offline evals passed 12/12; the site passed 54 tests with zero npm
  vulnerabilities and no lint errors.
- Three independent adversarial audits found no remaining payload, graph,
  schema, secret-boundary, cross-runtime, or packaging blocker. A three-candidate
  alternate-benchmark replay was rejected by the immutable cohort proof.
- The direct Grok MCP review attempt returned only a zero-token planning stub,
  and the optional hosted review runner remained unavailable; no Grok approval
  is claimed.
- The control-center site passed 58 tests and now rejects packaging unless all
  25 `public/**` assets exist byte-for-byte under `dist/client/**`. This guard
  caught a stale ignored build in Sites version 8; that version was superseded.
- Sites version 9 was built from protected merge `8965a46d`, contained all 19
  OKF documents, and deployed successfully. Both the Sites production URL and
  `https://grokmcp.org/docs/okf/intelligence-payload-semantics-v1.json` return
  HTTP 200 with SHA-256
  `7464c2343c3edaadc21a14a880e689ef8e4b4ac0fa3fc07b2b6f37b08733545a`.

## Trust boundary

- Canonical envelope validity, known-profile semantics, evidence bytes,
  complete graph closure, publication authentication, and promotion policy are
  separate gates. The envelope `signatures` array still authorizes nothing.
- Shared-text secret detection is pinned and fail-closed but remains defense in
  depth; Git push protection and publication policy are still required.
- Git is shared Insider truth. Any Insider SQLite is only a disposable,
  reconstructible materialized view. Public consumer SQLite remains private
  runtime truth.
- No tunnel, cloud callback into localhost, shared database, or browser-held
  xAI credential is part of this architecture.

## Deliberately separate next phases

- Wire the current contributor Swarm to emit GNO capsules without relabeling
  its discounted-UCB routing or wall-clock/`tracemalloc` results as stronger
  evidence.
- Build pinned `perf`/Callgrind OptiBench producers, signed local/cloud
  publishers, quarantine fetch, publication authentication, and promotion
  policy.
- Add the disposable Insider materializer and prove delete-and-rebuild recovery
  solely from trusted Git refs.
- Benchmark Needle nested-example conditioning against simpler retrieval before
  allowing it to remain even a non-authoritative shadow ranker.
- Build the single adaptive contributor/admin UI on these contracts; keep
  public visitors read-only and public MCP consumers headless in their IDE.

The public OKF bundle is deployed. No MCP package release, consumer-runtime
deployment, SQLite migration, or Insider producer/promotion activation is
attached to this landing.
