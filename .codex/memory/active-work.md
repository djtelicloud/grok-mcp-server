# Codex Active Work

Last updated: 2026-07-13
Owner: Codex
Status: Stage 0.5 provider wiring passed; Git integration and CI remain

This is the project-scoped handoff for new Codex chats. Verify drift-prone Git,
CI, runtime, DNS, cloud, and benchmark state live before acting. Never record
credentials, OAuth codes, tokens, or private keys here.

## Exact verified state

- Campaign Issue #65 and Gemini draft PR #66 remain the parent proposal. The
  Codex repair branch starts from Gemini head
  `8d99427d2719da286cb24245adbdedf656464070` and must be published as a
  stacked draft PR; it is not landed on `main`.
- Stage 0.5 securely binds Vertex through standard Google ADC and Grok through
  the loopback UniGrok MCP. No ADC file, API key, OAuth file, CLI auth, `.env`,
  Gemini config, Claude auth, or Codex auth was copied into a worktree.
- The owner-only non-secret provider profile lives under
  `~/Library/Application Support/UniGrok/campaigns/gemma-needle-2000-v1/`.
  Provider caches and receipts are external, mode `0700`/`0600`, schema- and
  provenance-validated, and digest-bound for integrity.
- Final live smoke passed with exactly two transport attempts, two verified
  provider responses, and zero dataset writes. Receipt digest:
  `sha256:d784e49ab87d891c44985f326072191cb51fce87b9edc10c013b21a236568a54`.
- Disconnected replay passed with dead ADC/network settings, two cache hits,
  zero transport attempts, zero provider responses, and zero dataset writes.
  Receipt digest:
  `sha256:b9c34b95aeea2bbd1c9df62c040528af52b763779b3ce0ead4ea6fefe2833a34`.
- Exact final local verification: 1,287 full-suite tests on Python 3.11; 65
  focused campaign/release tests on Python 3.12; Ruff and `git diff --check`
  green; Docker build green with only `.grok/prompts` and `.grok/hyperparams`
  present in the image.

## Remaining gates

- Commit and push `codex/campaign-provider-runtime`, open a stacked draft PR
  against `gemini/campaign-gemma-needle-2000-v1`, and link it to Issue #65 and
  PR #66 with the exact head and test/receipt evidence.
- Do not start Stage 1 generation until the stacked repair is integrated into
  the Gemini campaign branch and its exact head passes CI/Codex review.
- Do not represent the replay cache as authenticated against a malicious
  process already running as the same OS user. It is owner-private and
  digest-bound; stronger same-user resistance would require a Keychain-backed
  signing key.
- Local Gemma weights were not downloaded. Model acquisition remains a
  separately authorized, digest-pinned experiment.
