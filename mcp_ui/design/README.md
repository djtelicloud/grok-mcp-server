# UniGrok theme (brand → Grok TUI slots)

**Source of truth:** [https://grokmcp.org/](https://grokmcp.org/) and
`sites/unigrok-control-center/app/globals.css`.

Canonical design docs and install path live under
[`docs/design/unigrok-theme.md`](../../docs/design/unigrok-theme.md).

## Brand tokens

| Token | Hex |
| --- | --- |
| canvas | `#07121f` |
| cyan | `#55c7ff` |
| violet | `#8768ff` |
| green | `#5de168` |
| amber | `#ffbd18` |
| danger | `#ff7188` |
| text | `#f7f9fc` |

## Artifacts

| File | Role |
| --- | --- |
| [unigrok-grok-theme.toml](unigrok-grok-theme.toml) | Full Grok TUI slot map (`bg_*`, `accent_*`, `md_*`, …) |
| [../../docs/design/unigrok-grok-theme.json](../../docs/design/unigrok-grok-theme.json) | Machine-readable twin |
| [../../docs/design/UniGrok.terminal](../../docs/design/UniGrok.terminal) | Apple Terminal profile |

## Install

```bash
./scripts/install-unigrok-theme
./scripts/install-unigrok-theme --check
```

Copies design sources into `~/.grok/themes/` (`unigrok.toml`, `unigrok.json`,
`UniGrok.terminal`). Grok 0.2.x still only lists built-in theme names; files are
forward-ready for custom load. Import the Terminal profile for host brand match
today.
