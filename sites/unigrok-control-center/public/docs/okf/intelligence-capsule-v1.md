# UniGrok Insider IntelligenceCapsule v1

This protocol belongs to the **Insider Factory**: repository contributors,
admins, their agents, local contributor runtimes, and ephemeral cloud runners.
It does not replace or synchronize the public MCP consumer's private local
SQLite database.

## Separation of church and state

- Public consumers keep `grok_sessions.db` for private local session history,
  routing, jobs, telemetry, and workspace memory.
- Public-consumer SQLite bytes and rows never enter a capsule, Git ref, hosted
  control request, public statistic, or cloud execution context.
- Insider shared truth is the validated Git DAG under `refs/unigrok/*`.
- An Insider SQLite database may materialize that DAG for fast UI queries, but
  it is disposable. Deleting it and rebuilding from trusted refs must restore
  all shareable state.
- Local and cloud executors consume the same immutable subjects and emit the
  same capsule format. Neither executor calls back into the other.

## Canonical bytes and identity

Capsule bodies use a deliberately restricted domain of the JSON
Canonicalization Scheme in RFC 8785. This is not a general-purpose JCS library:
every value accepted by the UniGrok profile has the same RFC 8785 wire form,
while floats, nulls, non-ASCII keys, and other divergence-prone values are
outside the protocol:

1. strict UTF-8 without BOM;
2. ASCII `snake_case` object keys;
3. no duplicate keys, nulls, floats, negative zero, NaN, or infinities;
4. integers limited to `[-9007199254740991, 9007199254740991]`;
5. non-integral measurements encoded as plain decimal strings plus units;
6. metadata strings already normalized to Unicode NFC;
7. set-like arrays validated as unique and sorted;
8. inapplicable optional fields omitted rather than set to null.

Both implementations use the same explicit string escape table: quote and
backslash are escaped; backspace, tab, LF, form feed, and carriage return use
their short escapes; remaining U+0000 through U+001F code points use lowercase
`\u00xx`; every other valid Unicode scalar is emitted as raw UTF-8. Lone
surrogates are rejected. Python and TypeScript must not delegate this rule to a
runtime's default serializer.

Arbitrary source code and binary evidence are separate byte blobs and are never
Unicode-normalized. The capsule body refers to them by SHA-256 and, once stored,
their Git object id.

Given the exact JCS body bytes `B`, capsule identity is:

```text
D  = SHA-256(B)
ID = "ucap1:sha256:" + lowercase_hex(D)
```

The envelope digest covers `body` only. Adding a signature does not change the
capsule identity. Producers sort signatures by `(profile, key_id, value)`.

## Ordering rules

- `parents`: lexicographic capsule id.
- `evidence`: `(name, sha256)`.
- `metrics`: `(name, unit)`.
- `signatures`: `(profile, key_id, value)`.

JSON Schema cannot enforce Unicode normalization, canonical wire bytes, array
ordering, or digest recomputation. Producers and consumers must run the
semantic validators in `src/intelligence_capsule.py` or the corresponding
TypeScript module.

Network and file ingest must begin with the original bytes: call
`parse_canonical`, then `validate_envelope_integrity`. Do not call a framework's
JSON parser first, because doing so discards duplicate-key and alternate-wire
evidence. The parser rejects inputs larger than 1 MiB before UTF-8 decoding or
JSON parsing. Integrity validation proves structure and digest only; it does
not authenticate `actor` or verify the signatures array.

## Authorship and publication

`gh auth` authorizes GitHub operations; it does not sign arbitrary capsule
bytes. A locally published capsule must be contained in a verified SSH/GPG
signed Git commit. A cloud capsule must be bound to its workflow and source
commit through an approved attestation. Authentication establishes authorship,
not correctness; promotion still requires deterministic gates and repository
policy.

The envelope `signatures` field is structural and reserved in v1. Its profile
labels are not an authentication protocol: v1 intentionally does not define a
signed-message domain, key discovery, or trust-root policy for those entries.
Until a later version defines all three, consumers must ignore the field for
authorization. Verified signed Git commits and approved cloud attestations are
the only v1 publication-authentication paths.

`provenance.source_commit` identifies the exact UniGrok implementation that
generated the capsule. `subject.commit` identifies the project revision being
evaluated. They are intentionally allowed to differ and must never be presented
as the same claim.

## Local refs and remote projection

The canonical local namespace is:

```text
refs/unigrok/schema/v1
refs/unigrok/knowledge/verified
refs/unigrok/benchmarks/main
refs/unigrok/policies/active
refs/unigrok/failures/sanitized
refs/unigrok/proposals/<github-login>/<run-id>
refs/unigrok/runs/<github-login>/<run-id>
```

The bootstrap tool creates only the five fixed local heads. It has no remote
write mode. It reads the schema bytes from the last fetched public
`refs/remotes/origin/main`, never from a task worktree, and refuses bootstrap
when that direct source ref is unavailable. V1 pins the schema SHA-256 to
`10c2ec4638bd6c4e303b3e2c4c7d91ae582554f48aaa01fac2d9370062b98d4c`
and the SHA-1-format genesis Git object to
`6dadda28ac4174bf227f36b45917e15c663987ce`; changing either requires a new
protocol version. The schema ref points to that deterministic, zero-parent
genesis commit, so every clone converges on one object. The genesis object is
deliberately an identity anchor, not an authorship claim; signed descendant
commits and the promotion policy establish authorship.

Bootstrap rejects symbolic aliases for the source and all five fixed refs and
updates refs with dereferencing disabled. Its `ready` status means only that the
namespace is structurally complete, the genesis bytes are exact, and mutable
heads are descendant commits. It does not authenticate or promote descendant
content. Every reader must still apply the publication-authentication and
capsule-validation policy before treating any descendant as trusted knowledge.

GitHub transport should use protected `refs/heads/unigrok/*` as a compatibility
projection unless an explicit admin canary proves that native custom refs have
the required visibility, rules, and event behavior. Fetched remote intelligence
enters a quarantine ref first and is promoted to a trusted local ref only after
schema, digest, publication-authentication, ancestry, and bounds checks.

## Storage and recovery invariant

The future Insider materializer is correct only when this destructive test
passes:

```bash
rm -f unigrok-intelligence-view.db
unigrok intelligence rebuild
```

All shareable UI state must reappear solely from trusted Git refs. This command
must not open, migrate, copy, or modify the public consumer's
`grok_sessions.db`. The rebuild command is a reserved contract for the later
materialized-view phase; the v1 protocol gate does not add or alter a database.
