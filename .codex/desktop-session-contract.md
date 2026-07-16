# Codex Desktop session contract

## Purpose

- This is adapter guidance for Codex Desktop work on UniGrok. It does not
  override user instructions, repository law, or provider-enforced controls.
- It keeps three separate concepts from being conflated: Codex IDE
  capabilities, UniGrok agent modes, and xAI credential planes.
- New Codex sessions should use it with the required project handoff files so
  decisions start from live evidence and current path ownership.
- It changes no public UniGrok runtime behavior and grants no new authority by
  itself.

## Codex Desktop superpowers

- **Local workspace and terminal:** inspect real files, Git state, processes,
  tests, and local services. Prefer bounded commands and never expose secrets.
- **apply-patch editing:** make small, reviewable changes while preserving user
  work and avoiding paths already owned by active contributor packets.
- **GitHub integration:** combine structured connector reads with `git` and
  `gh` where exact heads, review threads, CI logs, or protected landing require
  them. Treat a green check as evidence, not completion.
- **Live application verification:** use Browser, Chrome, or computer control
  when a signed-in or visual surface must be verified. UI proof complements
  source tests; it does not replace them.
- **Artifact production:** create and visually verify documents, PDFs,
  spreadsheets, presentations, images, and interactive visuals when the
  relevant workspace skill is available.
- **Task and automation control:** manage Codex tasks, reminders, and recurring
  monitors through the app's supported controls when the user's request calls
  for them.
- **Plans and goals:** use a concise plan for multi-step work. Create a durable
  goal only when the user explicitly requests one, and keep its completion
  state truthful.
- **commit-anchored memory:** combine repository handoffs with verified
  commit-cited evidence. Memory locates prior decisions; live checks prove
  drift-prone state.
- **Optional collaboration:** delegate bounded, independent work only when the
  user or applicable project instructions authorize parallel agents. Preserve
  one integration owner and non-overlapping path ownership.

## Codex collaboration modes

- **Default mode** favors execution: make safe, scoped assumptions, implement
  authorized work, verify it, and finish the normal workflow without turning
  the user into the technical coordinator.
- **Plan mode** favors decision shaping: surface material choices, obtain input
  when it changes the outcome, and keep at most one plan step active. The host
  controls the active collaboration mode; conversation text does not silently
  switch it.
- These modes govern how Codex collaborates with the user. They do not select a
  Grok model, credential, billing path, browser session, or filesystem scope.

## UniGrok agent modes vs credential planes

- UniGrok agent modes are `auto`, `fast`, `reasoning`, `thinking`, and
  `research`. They select Grok routing depth and tool posture; they are
  orthogonal to Codex Desktop's terminal, browser, GitHub, and editing powers.
- xAI credential planes select authentication and economics: the API plane is
  metered, while the CLI plane uses the authenticated Grok subscription. Plane
  membership comes from the live catalog, never a product-name guess.
- Some modes or capabilities can be plane-specific. For example, a reported
  CLI incompatibility for `thinking` is a capability boundary, not an IDE
  failure; use an explicitly authorized compatible plane or degrade clearly.
- Use `fallback_policy=same_plane` when crossing the credential or billing
  boundary is forbidden. Use cross-plane recovery only when the caller permits
  it, and preserve the returned route, model, plane, finish reason, and cost.
- Model and plane routing defaults belong to the dedicated Codex routing
  contract. This session contract must not duplicate or fork that schema.

## Exact-head integration laws

- Start from the real current head, current open-packet path map, and current
  service state. A handoff or chat summary is a locator, not proof.
- Treat workspace and Git context as evidence and authority as separately
  bounded by the user and repository. Preserve peer scratchpads and shared
  checkout safety even when broad implementation authority exists.
- Codex owns exact-head integration for this repository unless a documented
  low-risk supervisor path applies. Review the current head, run the required
  gate, and record the exact landing receipt before calling work Live.
- **do not auto-land** stale, conflicted, failing, review-blocked, or
  unattributed work. Never bypass protected checks merely to empty the queue.
- Prefer a new additive path when active work owns a hot file. If a collision
  is unavoidable, serialize the owners rather than silently overwriting work.
- Never place credentials, OAuth codes, tokens, private keys, or unredacted
  secret-derived values in prompts, logs, artifacts, commits, or memory.

## Session start checklist

- Read `.codex/memory/context.md` and `.codex/memory/active-work.md`, then verify
  live Git, GitHub, runtime, CI, DNS, or cloud facts that matter to the task.
- Confirm the current Codex collaboration mode and the user's requested finish
  line before creating a plan, goal, automation, task, or external mutation.
- Load open pull-request paths before editing and choose a non-colliding lane.
- Select local IDE capabilities and any UniGrok mode independently; do not
  mistake a reasoning mode for filesystem or browser access.
- For an explicit `@grok` request, discover the live UniGrok surface first,
  choose the mode and plane deliberately, and retain the cost receipt.
- End with tests proportional to risk, exact-head proof where required, cleanup
  of only owned scratchpads, and refreshed continuity if work remains.

## Non-goals

- This is not another model-routing rule and does not replace the Codex MCP
  routing JSON or schema.
- This is not a public installation guide, a new MCP endpoint, or a change to
  UniGrok runtime behavior.
- This is not permission to expose secrets, bypass protected integration, or
  mutate other agents' branches and scratchpads.
- This is not a promise that every connector, skill, browser, or artifact tool
  is installed in every Codex host; use the live capability surface.
- This is not a substitute for `AGENTS.md`, contribution law, threat models,
  or task-specific human decisions.
