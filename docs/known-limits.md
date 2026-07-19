# Known limits

Honest notes on what shipped, what has had limited soak time, and how to file a
report that we can actually reproduce. One section per release, newest first.

## 1.1.0

### Hosted collaboration is authenticated, but state is instance-local

The owner-operated remote endpoint is an OAuth-gated collaboration pilot, not an
anonymous multi-tenant SaaS. OAuth identity isolates sessions, facts, jobs, telemetry,
and configured budgets, while hosted xAI file tools require a principal-bound provider
credential.

Cloud Run currently stores SQLite under `/tmp`. A revision or instance replacement can
therefore lose hosted sessions, facts, and durable-job state, and two scaled instances
do not share that state. Release cutovers are atomic rather than fractional for this
reason. Treat an unknown poll after replacement like `lost`: inspect provider state
before retrying any metered or mutating operation. Local Compose uses a persistent
volume and has the stronger restart behavior documented below.

Configured caller budgets are pre-call daily stop thresholds, not atomic reservations.
A call admitted just below its threshold, or several concurrent calls, can finish above
the configured amount. Use conservative thresholds and provider-side account controls
when a strict financial ceiling is required.

### Depth modes are new — here is what a "miss" looks like

`max` (deep harness) and `ultra` (hive voting) shipped in 1.1.0 and have had
limited soak time across task shapes. If one of these misbehaves for you, that
is exactly the feedback we need. A depth-mode miss usually looks like one of:

- **Preamble-only or non-answer output** — the result reads like the model
  announcing what it will do instead of doing it. 1.1.0 guards the fast routes
  against this and rejects such completions with an error; if a non-answer
  still reaches you as a *successful* result, report it.
- **Harness leakage** — internal harness or persona instructions showing up in
  the answer text.
- **A hive merge that discards a clearly better voter draft** — the
  `hive.stages` receipt shows each stage; include it.
- **Depth resolving lower than you asked for** — every result echoes the
  requested and resolved depth/level. If they differ and no documented
  downgrade rule applies, report it.

### Plane fallback is visible — check the receipts

In local Compose, the subscription CLI plane is the default; the metered xAI API plane is for
selected specialists and bounded recovery. Every result carries
`resolved_plane`, `fallback_occurred` / `fallback_reason`, and `usage`. If
those receipts show a fallback to the API plane that you did not expect, file
a bug with those fields — they tell most of the story.

### Expected behavior, not bugs

- **`xhigh` on the API plane auto-downgrades to `high`** instead of erroring.
  The downgrade is echoed in the resolved level.
- **On persistent local Compose, Mission V2 and generic jobs recover differently after
  restart.** Mission V2
  keeps durable mission truth and returns `continue` with the same token (or the
  canonical terminal winner). A generic job whose terminal result was already
  recorded remains pollable; one interrupted before recording returns `lost`.
  `lost` means the provider outcome is unknown, so inspect provider state before
  retrying a metered or mutating operation. Durable facts and session history are
  unaffected.

### Auto++ router mis-routes

In local Compose, unclear tasks route via three
CLI-first intent votes. A provider failure may cross to the metered API, and an
inconclusive vote set may use the bounded semantic API fallback; receipts report
the actual plane and cost of every attempt. If it chooses a depth or voter count
that is obviously wrong for your task, report it and include the task text —
routing quality is tuned from real task shapes.

### How to report

Use the [bug report form](https://github.com/djtelicloud/grok-mcp-server/issues/new?template=bug_report.yml). The form
asks for the three things that make depth/hive reports reproducible:

1. the `level` / `depth` you set (or "unset" if you let UniGrok choose),
2. the receipt fields from the result — `resolved_plane`,
   `fallback_occurred` / `fallback_reason`, `resolved_depth`, `usage`, and
   `hive.stages` if present,
3. the task text (redacted as needed) and steps to reproduce.

For router mis-routes, the task text plus the requested and resolved depth is
the minimum useful report. Per-IDE onboarding problems (a client that never
connects, a merge entry written to the wrong path) use the same bug form —
include the IDE and the install path involved; those are setup issues, not
depth misses.

**Do not paste** API keys, tokens, private source code, or full system
prompts into a report. Receipts and redacted task text are enough.
