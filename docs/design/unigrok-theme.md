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

## Install path (`~/.grok/themes/`)

Grok user config lives under `~/.grok` (override with `GROK_HOME`). Install the
brand slot map to the canonical custom-theme directory:

```bash
./scripts/install-unigrok-theme
./scripts/install-unigrok-theme --check
```

| Destination | Source |
| --- | --- |
| `~/.grok/themes/unigrok.toml` | `docs/design/unigrok-grok-theme.toml` |
| `~/.grok/themes/unigrok.json` | `docs/design/unigrok-grok-theme.json` |
| `~/.grok/themes/UniGrok.terminal` | `docs/design/UniGrok.terminal` |

Options:

| Flag | Meaning |
| --- | --- |
| `--check` | Verify installed files match design sources and required slots |
| `--dry-run` | Print planned copies without writing |
| `--force` | Overwrite drifted install files |
| `--enable-config` | Also set `[ui] theme = "unigrok"` in `config.toml` (backs up first) |
| `--grok-home PATH` | Alternate Grok home (tests / multi-user) |

The installer never spends API credits and does not touch Docker.

## Grok Build limitation (0.2.x)

Grok Build **0.2.x** ships five **built-in** theme names and does **not** load
`unigrok` from disk yet (see local user guide `~/.grok/docs/user-guide/06-theming.md`).
Until custom themes exist:

1. Run `./scripts/install-unigrok-theme` so files sit at the future load path.
2. Import **UniGrok.terminal** so the host shell matches brand navy + cyan.
3. Keep the slot map as the contract for a future `/theme unigrok`.
4. Prefer TokyoNight among stock skins only as a temporary blue stand-in — it
   is **not** brand-correct.

When Grok begins loading `~/.grok/themes/*.toml`, re-run `--check`, then either
`/theme unigrok` or `./scripts/install-unigrok-theme --enable-config`.

## Smoke

```bash
# From a product checkout that includes docs/design/unigrok-*
./scripts/install-unigrok-theme --dry-run
./scripts/install-unigrok-theme --force
./scripts/install-unigrok-theme --check
uv run pytest -q tests/test_install_unigrok_theme.py
```

Expected: three files under `$GROK_HOME/themes/` (default `~/.grok/themes/`),
`name = "unigrok"`, required TUI slots present as `#RRGGBB`.
