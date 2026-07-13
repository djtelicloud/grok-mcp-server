# Human and agent attribution

UniGrok keeps two kinds of credit separate:

- A real GitHub user or verified GitHub bot is an accountable contributor.
- An AI provider, product, or model is traceable assistance unless it has an
  exact verified GitHub identity in
  [`.github/agent-identities.json`](../.github/agent-identities.json).

The Git commit author and the pull request's **Accountable GitHub
contributor** remain responsible for the contribution. Never create an email,
GitHub login, or `Co-authored-by` identity for a model.

## Canonical trailers

Material implementation, research, documentation, testing, or integration
help uses a repeatable `Agent-Assisted-By` trailer. Advisory review uses
`Agent-Reviewed-By`:

```text
Agent-Assisted-By: OpenAI Codex | model=GPT-5 | model-source=user-reported | surface=Codex Desktop | role=implementation
Agent-Assisted-By: Anthropic Claude | model=Fable 5 Ultracode | model-source=user-reported | surface=Claude Code | role=research
Agent-Assisted-By: Google Gemini | model=Gemini 3.1 Pro | model-source=user-reported | surface=Antigravity | role=data-design
Agent-Reviewed-By: xAI Grok | model=grok-4.5 | model-source=runtime-receipt | surface=UniGrok CLI | role=advisory-review | evidence=receipt-sha256:0123456789abcdef
```

Use one trailer per materially involved agent. Repeating either trailer is
allowed. Values have this form:

```text
<registered provider product> | model=<value> | model-source=<source> | surface=<value> | role=<registered role> [| evidence=<value>]
```

Allowed model sources are:

- `runtime-receipt`: a runtime receipt reported the model identifier;
- `provider-session`: the provider-owned session reported the model;
- `user-reported`: a human or contributor supplied the label without a
  machine-verifiable receipt;
- `unverified`: the exact model is unknown, written as
  `model=unverified | model-source=unverified`.

Do not infer a model version from a product name. In particular, a product UI,
an API model slug, and a CLI catalog identity may differ.

`Agent-*` trailers intentionally contain no email address and do not create
GitHub contributor status. They remain visible in the commit and pull request
record as honest provider/model credit.

## `Co-authored-by` boundary

Use `Co-authored-by: Name <linked-email>` only for:

1. a real person whose email is linked to their GitHub account; or
2. an exact bot identity listed under `verified_github_agent_identities` in
   the registry.

For example, `Claude <noreply@anthropic.com>` is not a GitHub identity and is
forbidden. Local Copilot, Codex, Claude, Gemini, and Grok sessions use
`Agent-Assisted-By` or `Agent-Reviewed-By` even if a similarly branded GitHub
App exists. A verified GitHub bot identity applies only to work actually
performed by that bot.

The repository contains legacy synthetic AI coauthor trailers. Shared history
is not rewritten. They are historical text, not verified account mappings,
and the bounded checker prevents new occurrences.

## Enforcement

Run:

```bash
python scripts/check_agent_attribution.py --base-ref origin/main --head HEAD
```

The checker uses `git interpret-trailers`, validates only commits introduced
after the merge base, allows repeated canonical agent trailers, and rejects an
AI-branded Git author or coauthor unless it exactly matches the verified
registry. The bounded range means legacy history does not block current work.

CI runs the same check on the pull request's base and head SHAs. The Codex
landing gate runs it again against current local `main` before tests.
