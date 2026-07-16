# Public pack v0 — Terminal markdown authoring

**Audience:** public installers and contributor agents talking in a **terminal**  
**Pack id:** `terminal-markdown-authoring` · **version:** `v0`

How agents should shape user-facing replies when the host is a CLI/TUI (Grok
Build, Claude Code CLI, Codex CLI, plain SSH) — not a browser HTML panel.

This pack is about **structure** (markdown you write). It is **not** about
picking theme colors. Human radio (**Ready for supervisor** / **Live** / brand
+ **Task titles, not numbers**) is the content law; this pack is the layout law.

## 1. Two layers (do not mix them up)

| Layer | Owner | What it is |
| --- | --- | --- |
| **Authoring** | Every terminal agent | Portable GFM/CommonMark structure in the reply |
| **Paint** | Host theme / terminal | Colors for headings, code, links (e.g. Grok `md_*` theme slots) |

Agents choose headings, lists, tables, fences, and links. Users choose themes.
Do not invent a private color language or dump HTML/CSS as the primary UI.

## 2. Portable terminal markdown (all terminal agents)

Use this shape for most finish and status replies:

1. **One lead line** — brand + status + plain task title (human radio).  
2. **Optional short bullets or one small table** — only what the human needs.  
3. **Optional fenced code** — errors, commands, excerpts.  
4. **Optional path or link** — open elsewhere; do not fake a browser in chat.

### Prefer

- Short status lines and sparse bullets  
- Small tables (few columns, short cells)  
- Fenced blocks for multi-line source, logs, or commands  
- File paths and `https://` links for “open this”  
- One H2 (or none) and few H3s — deep heading trees waste width  

### Avoid

- Raw HTML/CSS as the primary chat UI (no in-stream webview)  
- Progress essays, tool dumps, full diffs unless the human asked  
- Huge tables, wide ASCII art, and walls of unfenced log  
- Leading with ticket numbers instead of plain task titles  
- Inventing host-only markup that other CLIs will show as garbage  

### Width and monospaced reality

Terminal scrollbacks fold, wrap, and truncate. Write for ~80–100 columns of
useful content. Put long artifacts on disk and point to the path.

## 3. Host appendix — Grok Build CLI/TUI

When the host is **Grok Build** (Grok CLI TUI):

- Chat is **GFM markdown** painted by the active theme (`md_heading_*`,
  `md_code`, `md_text`, `md_muted`, `link_fg`, task checkboxes).  
- User key **`r`** toggles raw markdown on a scrollback entry.  
- **Images:** short session paths (e.g. `images/1.jpg`) after image tools —
  clickable open, not embedded HTML.  
- **Real HTML pages / exact layout demos:** write a `.html` file and open it in
  the system browser; do not paste a full document into chat expecting a page.  
- There is **no** general HTML artifact panel in the TUI.

## 4. Host footnote — other terminals

| Host | Note |
| --- | --- |
| Claude Code / Codex CLI | Their own markdown paint; same portable authoring still applies |
| Plain SSH / bare terminal | Assume least: plain GFM, no fancy widgets |
| Cursor / VS Code chat | May be richer; this pack is optional there — do not force TUI rules onto GUI-only surfaces |

## 5. Worked examples

**Good (terminal finish)**

```text
Grok: Ready for supervisor — terminal markdown authoring pack.

- Docs-only; portable GFM + Grok host note
- Tests: public pack suite green
```

**Bad**

```text
Here is the full HTML for the dashboard:
<html><head><style>…thousands of lines…</style></head>…
Also I ran 40 tools and here is every log line…
```

**Good (need a real page)**

```text
Grok: Ready for supervisor — dashboard mock HTML on disk.

Open: docs/mocks/dashboard-preview.html
(or: open that path in your browser)
```

## 6. How this fits human radio

| Concern | Guide |
| --- | --- |
| What words to use | Human radio (`Ready` / `Live` / brand / plain titles) |
| How to shape the text | **This pack** |
| What colors appear | User theme / host — not the agent |

Silent human radio still wins: tools and diffs stay off-chat unless asked.
When you *do* speak, use this layout so every terminal host stays readable.

## 7. Promote habit

After a layout/gym win is **Live**, ask once: promote or bump this pack?
Keep scrub rules: no secrets, no private paths, no raw memory.
