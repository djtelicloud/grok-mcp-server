// Zero-dependency, escape-first Markdown renderer shared by the OKF viewer
// and the agent transcript. The entire source is entity-escaped before any
// fixed tags are inserted, so document- or model-authored HTML can never
// reach the DOM as markup. Fenced code blocks are extracted first and inline
// code spans are tokenized before emphasis/link passes, so neither pass can
// mangle the other. Callers inject the result via DOMParser.

// NUL delimits placeholder tokens; it cannot appear in the source because it
// is stripped from the input up front.
const FENCE_TOKEN = (index) => `\u0000F${index}\u0000`;
const CODE_TOKEN = (index) => `\u0000C${index}\u0000`;

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Only plain web and same-origin destinations survive; any other scheme
// (javascript:, data:, vbscript:) and protocol-relative URLs render as text.
export function sanitizeHref(url) {
  const trimmed = url.trim();
  // A control char lets an attacker hide a scheme from the anchored test below
  // while the browser's URL parser still strips it and resolves the scheme, so
  // reject any href containing one outright (input is also stripped up front).
  if (/[\u0000-\u001F\u007F]/.test(trimmed)) return null;
  if (/^(https?:\/\/|mailto:)/i.test(trimmed)) return trimmed;
  if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) return null;
  if (trimmed.startsWith("//")) return null;
  return trimmed;
}

function linkTag(url, label) {
  const href = sanitizeHref(url);
  if (!href) return label;
  return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
}

// Inline transforms for one already-escaped block of text. Code spans are
// tokenized out first so emphasis/link syntax inside backticks stays literal.
function renderInline(text, codeSpans) {
  let out = text.replace(/`([^`\n]+)`/g, (_match, body) => {
    codeSpans.push(`<code>${body}</code>`);
    return CODE_TOKEN(codeSpans.length - 1);
  });
  // Images degrade to links: the gateway CSP blocks cross-origin loads anyway.
  // URL groups are length-bounded so a run of unclosed "[x](" openers cannot
  // drive quadratic backtracking on a large hostile answer.
  out = out.replace(/!\[([^\]]*)\]\(([^)\s]{1,2048})(?:\s+&quot;[^)]*&quot;)?\)/g, (_m, label, url) => linkTag(url, label || url));
  out = out.replace(/\[([^\]]+)\]\(([^)\s]{1,2048})(?:\s+&quot;[^)]*&quot;)?\)/g, (_m, label, url) => linkTag(url, label));
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*\w])\*([^*\n]+)\*(?=[^*\w]|$)/g, "$1<em>$2</em>");
  out = out.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  out = out.replace(/\\\|/g, "|"); // an escaped table pipe renders literally
  return out;
}

export function parseMarkdown(markdown) {
  const source = String(markdown ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "");
  let text = escapeHtml(source);

  // Extract fenced blocks before any other pass, scanning line by line so the
  // opener must begin a line (only leading whitespace before ```): a mid-line
  // ``` in prose is left for the inline pass, not treated as a block opener.
  // The closer must be a whitespace-only-then-backticks line indented no more
  // than the opener, so a ``` sitting inside indented code content does not
  // close the block early. An unterminated fence (truncated answer) runs to
  // end of input. Fences nested in list items render de-indented at top level.
  const fences = [];
  const collapsed = [];
  const srcLines = text.split("\n");
  for (let i = 0; i < srcLines.length; i += 1) {
    const opener = srcLines[i].match(/^(\s*)```(.*)$/);
    if (!opener) {
      collapsed.push(srcLines[i]);
      continue;
    }
    const openIndent = opener[1].length;
    const lang = (opener[2].trim().match(/^[\w-]+/) || [""])[0];
    const body = [];
    let j = i + 1;
    for (; j < srcLines.length; j += 1) {
      const closer = srcLines[j].match(/^(\s*)```[ \t]*$/);
      if (closer && closer[1].length <= openIndent) break;
      // Drop up to the opener's indentation so list-nested code is not skewed.
      body.push(srcLines[j].replace(new RegExp(`^\\s{0,${openIndent}}`), ""));
    }
    fences.push(`<pre><code${lang ? ` class="language-${lang}"` : ""}>${body.join("\n")}</code></pre>`);
    collapsed.push(FENCE_TOKEN(fences.length - 1));
    i = j; // skip the closing fence line (or end of input)
  }
  text = collapsed.join("\n");

  const codeSpans = [];
  const blocks = [];
  let paragraph = [];
  let list = null;
  let quote = [];
  let table = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push(`<p>${renderInline(paragraph.join(" "), codeSpans)}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    const items = list.items.map((item) => `<li>${renderInline(item, codeSpans)}</li>`).join("");
    blocks.push(`<${list.tag}>${items}</${list.tag}>`);
    list = null;
  };
  const flushQuote = () => {
    if (!quote.length) return;
    blocks.push(`<blockquote><p>${renderInline(quote.join(" "), codeSpans)}</p></blockquote>`);
    quote = [];
  };
  // Split a table row on structural pipes only: a pipe inside an inline-code
  // span or escaped as \| stays part of the cell text.
  const splitCells = (line) => {
    const cells = [];
    let buf = "";
    let inCode = false;
    for (let index = 0; index < line.length; index += 1) {
      const ch = line[index];
      if (ch === "\\" && line[index + 1] === "|") {
        buf += "\\|";
        index += 1;
        continue;
      }
      if (ch === "`") {
        inCode = !inCode;
        buf += ch;
        continue;
      }
      if (ch === "|" && !inCode) {
        cells.push(buf);
        buf = "";
        continue;
      }
      buf += ch;
    }
    cells.push(buf);
    return cells;
  };
  const flushTable = () => {
    if (!table) return;
    const rows = table.map((line) => {
      const trimmed = line.trim().replace(/^\|/, "").replace(/\|[ \t]*$/, "");
      return splitCells(trimmed).map((cell) => cell.trim());
    });
    // The row above the first all-dashes separator is the header; the
    // separator itself never renders.
    const separatorIndex = rows.findIndex(
      (cells) => cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell))
    );
    const headerRows = separatorIndex > 0 ? rows.slice(0, separatorIndex) : [];
    const bodyRows = separatorIndex >= 0 ? rows.slice(separatorIndex + 1) : rows;
    const renderRow = (cells, tag) =>
      `<tr>${cells.map((cell) => `<${tag}>${renderInline(cell, codeSpans)}</${tag}>`).join("")}</tr>`;
    let out = "<table>";
    if (headerRows.length) {
      out += `<thead>${headerRows.map((cells) => renderRow(cells, "th")).join("")}</thead>`;
    }
    out += `<tbody>${bodyRows.map((cells) => renderRow(cells, "td")).join("")}</tbody></table>`;
    blocks.push(out);
    table = null;
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
    flushQuote();
    flushTable();
  };

  for (const line of text.split("\n")) {
    const fenceMatch = line.match(/^\u0000F(\d+)\u0000$/);
    if (fenceMatch) {
      flushAll();
      blocks.push(fences[Number(fenceMatch[1])]);
      continue;
    }
    if (/^\|.*\|\s*$/.test(line)) {
      flushParagraph();
      flushList();
      flushQuote();
      (table ??= []).push(line);
      continue;
    }
    if (table) flushTable();

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushAll();
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInline(heading[2].trim(), codeSpans)}</h${level}>`);
      continue;
    }
    if (/^(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushAll();
      blocks.push("<hr />");
      continue;
    }
    const quoted = line.match(/^&gt;\s?(.*)$/);
    if (quoted) {
      flushParagraph();
      flushList();
      quote.push(quoted[1]);
      continue;
    }
    const bullet = line.match(/^\s{0,3}[-*+]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      flushQuote();
      if (!list || list.tag !== "ul") {
        flushList();
        list = { tag: "ul", items: [] };
      }
      list.items.push(bullet[1]);
      continue;
    }
    const numbered = line.match(/^\s{0,3}\d{1,3}[.)]\s+(.*)$/);
    if (numbered) {
      flushParagraph();
      flushQuote();
      if (!list || list.tag !== "ol") {
        flushList();
        list = { tag: "ol", items: [] };
      }
      list.items.push(numbered[1]);
      continue;
    }
    if (!line.trim()) {
      flushAll();
      continue;
    }
    if (list && /^\s{2,}/.test(line)) {
      list.items[list.items.length - 1] += ` ${line.trim()}`;
      continue;
    }
    flushList();
    flushQuote();
    paragraph.push(line.trim());
  }
  flushAll();

  return blocks.join("\n").replace(/\u0000C(\d+)\u0000/g, (_match, index) => codeSpans[Number(index)]);
}
