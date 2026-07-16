# UniGrok theme (brand → Grok TUI slots)

**Source of truth:** [https://grokmcp.org/](https://grokmcp.org/) and
`sites/unigrok-control-center/app/globals.css`.

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
| [unigrok-grok-theme.json](unigrok-grok-theme.json) | Same, machine-readable |
| [UniGrok.terminal](UniGrok.terminal) | Apple Terminal profile (import or double-click) |

## Grok Build limitation (0.2.x)

Grok Build only ships five **built-in** theme names. It does **not** load
`unigrok` from disk yet. Until custom themes exist:

1. Install **UniGrok.terminal** so the host shell matches brand navy + cyan.
2. Keep the slot map as the contract for a future `/theme unigrok`.
3. Prefer TokyoNight among stock skins only as a temporary blue stand-in — it
   is **not** brand-correct.

Local install path used by Grok CLI sessions: `~/.grok/themes/unigrok.toml`.
