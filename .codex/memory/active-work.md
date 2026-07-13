# Codex Active Work

Last updated: 2026-07-13
Owner: Codex
Status: Conditional closeout for PR #61; verify live reachability on protected main

This is the project-scoped handoff for new Codex chats. Resolve drift-prone
Git, CI, runtime, DNS, cloud, and benchmark state live before acting. Never
record credentials, OAuth codes, tokens, or private keys here.

## Completed integration state

- PR #60 merged to protected `origin/main` as
  `faa69801d90a7783571ccc675a5d60ce6481f984`. Its exact reviewed and locally
  landed head was `d7edfc6c1faa471581d5c0785b0b95474b23b57e`.
- PR #60 preserves the useful Gemini and Claude contributions from drafts #42
  and #43, with corrected discovery, permissions, session, identity, cost, and
  supported-tool contracts. Drafts #42 and #43 are closed as superseded.
- PR #61 integrates draft #55's unified Control Center at contributor head
  `1ea5f72c560325cacd8f44532baa5107635df2fe`. The reviewed implementation is
  preserved by commits `a71bb6320d7f14349073cae8863c44d852650885` and
  `abdf871c124125d339ea212e3702c35c95e1a703`.
- PR #61 prevents stale cached UI assets from discarding completed Grok
  answers, version-locks Control Center and Swarm assets, improves responsive
  layout and onboarding, and keeps hosted protected data behind GitHub-backed
  authorization while rendering public signed-out context.
- No contributor worktree or untracked user file was removed.

## Verification evidence

- The PR #60 landing gate printed
  `LANDED TO MAIN: d7edfc6c1faa471581d5c0785b0b95474b23b57e`
  after 1,222 tests. All 11 exact-head GitHub checks passed.
- PR #61's reviewed implementation passed 1,228 Python tests, 127 focused
  UI/HTTP tests, 21 relevant hosted-site tests, and 59 canonical deployment
  tests. Independent exact-head review found no blocker.
- Live browser checks covered Control Center and Swarm at 1,280, 500, and 375
  pixels with no horizontal overflow or console errors. A live zero-config
  Grok sample completed on the CLI plane with a routing receipt.

## Live completion rule

- If this handoff commit is not yet reachable from `origin/main`, PR #61's only
  remaining gates are exact-head CI and Codex Approval, `./scripts/land`, the
  protected merge, closing superseded draft #55, and synchronizing local main.
- If this handoff is reachable from `origin/main`, PR #61 is merged, draft #55
  should be closed, and local main should match `origin/main`; verify those
  drift-prone facts live. Once they agree, this sweep has no active repository
  gate.

## Existing external evidence boundary

- CursorBench 3.2 uses Cursor's private/internal task suite and has no public
  submission harness. An official leaderboard result or `#1` claim still
  requires Cursor to evaluate UniGrok or provide an accepted interface.
- Public claims must distinguish the reproducible internal suite and live
  golden-target results from an official CursorBench ranking.
