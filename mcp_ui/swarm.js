// Swarm Optimizer — Pareto Playground.
// Renders unigrok-swarm-status-v2 plus recorded v1 exports (live via
// get_swarm_status view="json", or a static JSON export — identical
// rendering is the local/public symmetry). No frameworks, no CDN, no
// simulated data: unmeasured candidates stack in a gutter instead of being
// plotted at invented coordinates.
//
// Layout is the shared fluid container-query system (styles.css); this module
// only owns behavior: the resizable detail splitter, the responsive/clamped
// Pareto SVG, and the run flow that consumes a structured task_id (with a
// list_swarm_tasks poll fallback — never a regex scrape of prose).

"use strict";

// Must match src/version.py UI_ASSET_VERSION and the query token on the
// swarm.js reference in swarm.html. The sample uses the same token so a
// revalidated page cannot pair new logic with a heuristically cached payload.
const UI_ASSET_VERSION = "grok-v0.6.0-r13";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
// Colors come straight from the shared tokens (tokens.css). The old page
// re-aliased --green/--gray/--text/--muted onto tokens; this uses the real
// token names directly so there is one palette, not two.
const COLORS = {
  static_wall: "var(--red)",
  test_wall: "var(--orange)",
  dominated: "var(--ink-faint)",
  pareto_elite: "var(--teal)",
};
const STATUS_FORMATS = new Set(["unigrok-swarm-status-v1", "unigrok-swarm-status-v2"]);

const state = {
  payload: null,
  source: null,
  maxGen: 1,
  shownGen: 1,
  playing: null,
  runtimeMode: "unknown",
  lastChartWidth: 0,
};

const clampN = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// ── MCP plumbing (same JSON-RPC shape the Control Center uses) ──────────────
// Same-origin session: the browser sends its own cookies/credentials to /mcp.
// There is no manual bearer-token field (removed for single-origin honesty).

let rpcId = 1;
async function mcpCall(toolName, args) {
  const headers = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "X-Client-ID": "mcp-ui-swarm",
  };
  const res = await fetch("/mcp", {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      method: "tools/call",
      params: { name: toolName, arguments: args },
      id: rpcId++,
    }),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${text.slice(0, 300)}`);
  // Server may answer plain JSON or a single SSE data: frame.
  const jsonText = text.startsWith("event:") || text.startsWith("data:")
    ? text.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5)).join("")
    : text;
  const rpc = JSON.parse(jsonText);
  if (rpc.error) throw new Error(rpc.error.message || "MCP error");
  const content = rpc.result?.content?.[0]?.text ?? "";
  if (rpc.result?.isError) throw new Error(content || "MCP tool failed");
  return content;
}

// ── Loading ──────────────────────────────────────────────────────────────────

async function loadLive() {
  const field = $("taskId");
  const taskId = field ? field.value.trim() : "";
  if (!taskId) { setMsg("enter a task id", true); return; }
  setMsg("loading…");
  try {
    const raw = await mcpCall("get_swarm_status", { task_id: taskId, view: "json" });
    const payload = JSON.parse(raw);
    if (payload.error) { setMsg(payload.error, true); return; }
    setPayload(payload, "live", "live");
  } catch (err) {
    setMsg(String(err.message || err), true);
  }
}

function loadFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const payload = JSON.parse(String(reader.result));
      if (!STATUS_FORMATS.has(payload.format)) {
        setMsg("not a supported UniGrok swarm export", true);
        return;
      }
      setPayload(payload, `export: ${file.name}`, "export");
    } catch (err) {
      setMsg(`unreadable export: ${err.message}`, true);
    }
  };
  reader.readAsText(file);
}

function setMsg(text, isError) {
  const el = $("loadMsg");
  if (!el) return;
  el.textContent = text;
  el.className = isError ? "err" : "muted";
}

function setPayload(payload, sourceLabel, source) {
  payload = normalizePayload(payload);
  state.payload = payload;
  state.source = source;
  state.maxGen = Math.max(1, ...(payload.generations || []).map((g) => g.generation));
  state.shownGen = state.maxGen;
  const slider = $("genSlider");
  if (slider) {
    slider.max = String(state.maxGen);
    slider.value = String(state.maxGen);
  }
  setMsg(`loaded (${sourceLabel})`);
  const badge = $("sourceBadge");
  if (badge) {
    badge.textContent = source === "live" ? "live gateway data" : sourceLabel;
    badge.className = source === "live" ? "source-badge live" : "source-badge";
  }
  // Each render step is independent diagnostics; one failing panel must not
  // blank the rest of the page.
  for (const step of [renderScorecard, renderTradeoffSummary, renderChart]) {
    try {
      step();
    } catch (err) {
      console.error(`${step.name} failed:`, err);
    }
  }
  const candidates = (payload.generations || []).flatMap((generation) => generation.candidates || []);
  const leadingFrontId = payload.pareto_front?.[0];
  const firstUseful = candidates.find((candidate) => candidate.candidate_id === leadingFrontId && candidate.code)
    || candidates.find((candidate) => candidate.outcome === "pareto_elite" && candidate.code)
    || candidates.find((candidate) => candidate.code)
    || candidates[0];
  const detail = $("detail");
  if (firstUseful) renderDetail(firstUseful);
  else if (detail) detail.innerHTML = '<div class="muted">No candidates have landed yet.</div>';
}

function normalizePayload(payload) {
  if (!STATUS_FORMATS.has(payload?.format)) {
    throw new Error("unsupported UniGrok swarm status format");
  }
  if (payload.format === "unigrok-swarm-status-v1") {
    return {
      ...payload,
      input_kind: payload.input_kind || "workspace",
      search_strategy: payload.search_strategy || "baseline_batch",
      primary_goal: payload.primary_goal || "balanced",
      champion_id: payload.champion_id || payload.pareto_front?.[0] || null,
      analytics: payload.analytics || null,
    };
  }
  return payload;
}

// ── Scorecard ────────────────────────────────────────────────────────────────

function fmtPct(v) { return v == null ? "n/a" : `${Number(v).toFixed(1)}%`; }
function fmtUsd(v) { return v == null ? "n/a" : `$${Number(v).toFixed(4)}`; }
function fmtLatencyImprovement(v) {
  if (v == null) return "n/a";
  return v >= 0 ? `${fmtPct(v)} faster` : `${fmtPct(Math.abs(v))} slower`;
}
function fmtMemoryImprovement(v) {
  if (v == null) return "n/a";
  return v >= 0 ? `${fmtPct(v)} less` : `${fmtPct(Math.abs(v))} more`;
}

function renderScorecard() {
  const board = $("scorecard");
  if (!board) return;
  const p = state.payload;
  const agg = p.aggregates || {};
  const oracle = p.oracle || {};
  const bench = (oracle.bench || {});
  const cards = [
    ["status", `${p.status} (${p.mode})`],
    ["strategy", p.search_strategy || "baseline_batch"],
    ["primary goal", p.primary_goal || "balanced"],
    ["feasibility rate", agg.feasibility_rate == null ? "n/a"
      : `${(agg.feasibility_rate * 100).toFixed(0)}% of ${agg.candidates_total}`],
    ["best latency", fmtLatencyImprovement(agg.best_latency_improvement_pct)],
    ["memory impact", fmtMemoryImprovement(agg.best_memory_improvement_pct)],
    // Budget ceiling is shown before any Run: spent / ceiling.
    ["cost to optimize", `${fmtUsd(agg.cost_to_optimize_usd)} / $${(p.budget?.budget_usd ?? 0).toFixed(2)}`],
    ["focus coverage", oracle.focus_coverage_pct == null ? "n/a" : `${oracle.focus_coverage_pct}%`],
    ["bench", `${bench.stability || "n/a"} (floor ${bench.noise_floor_pct ?? "?"}%)`],
    ["generations", String(p.budget?.generations_run ?? 0)],
  ];
  board.innerHTML = cards
    .map(([k, v]) => `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`)
    .join("");
}

function renderTradeoffSummary() {
  const el = $("tradeoffSummary");
  if (!el) return;
  const agg = state.payload?.aggregates || {};
  const latency = fmtLatencyImprovement(agg.best_latency_improvement_pct);
  const memory = fmtMemoryImprovement(agg.best_memory_improvement_pct);
  el.textContent = latency === "n/a"
    ? "No benchmark-qualified candidate is available yet."
    : `Pareto readout: the fastest verified candidate is ${latency} and uses ${memory} peak memory. Neither axis is hidden.`;
}

function esc(value) {
  return String(value).replace(/[&<>"]/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}

// ── Chart ────────────────────────────────────────────────────────────────────
// The SVG is responsive (viewBox measured from the pane's own width) AND safe:
// every coordinate is clamped inside the plot box, and the unmeasured "walls"
// gutter is capped to what fits and rolled up into a "+N more" marker so no dot
// ever marches off-canvas.

function candidatesUpTo(gen) {
  return (state.payload.generations || [])
    .filter((g) => g.generation <= gen)
    .flatMap((g) => g.candidates || []);
}

function chartMetrics(svg) {
  const rect = svg.getBoundingClientRect ? svg.getBoundingClientRect() : { width: 0 };
  const W = clampN(Math.round(rect.width) || 720, 360, 1400);
  const H = clampN(Math.round(W * 0.52), 300, 460);
  const compact = W < 560;
  const M = {
    l: compact ? 52 : 70,
    r: compact ? 14 : 20,
    t: 16,
    b: compact ? 36 : 42,
  };
  const ticks = compact ? 3 : 4;
  const gutterX = clampN(M.l - (compact ? 28 : 36), 14, M.l - 12);
  return { W, H, M, ticks, gutterX, compact };
}

function renderChart() {
  const svg = $("chart");
  if (!svg) return;
  svg.innerHTML = "";
  const p = state.payload;
  if (!p) return;
  const { W, H, M, ticks, gutterX, compact } = chartMetrics(svg);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  state.lastChartWidth = W;

  const shown = candidatesUpTo(state.shownGen);
  const measured = shown.filter((c) => c.feasible && c.latency_ms != null);
  const walls = shown.filter((c) => !(c.feasible && c.latency_ms != null));
  const baseline = p.baseline || {};

  const xs = measured.map((c) => c.latency_ms).concat(baseline.latency_ms ?? []);
  const ys = measured.map((c) => c.peak_mem_bytes).concat(baseline.peak_mem_bytes ?? []);
  const [x0, x1] = pad(Math.min(...xs, Infinity), Math.max(...xs, -Infinity));
  const [y0, y1] = pad(Math.min(...ys, Infinity), Math.max(...ys, -Infinity));
  const sx = (v) => clampN(M.l + ((v - x0) / (x1 - x0)) * (W - M.l - M.r), M.l, W - M.r);
  const sy = (v) => clampN(H - M.b - ((v - y0) / (y1 - y0)) * (H - M.t - M.b), M.t, H - M.b);

  drawAxes(svg, x0, x1, y0, y1, sx, sy, W, H, M, ticks);

  // Gutter stack: killed-before-measurement candidates, deterministic order.
  // Capped to the plot height with a "+N more" rollup so nothing draws off the
  // canvas; every y is clamped inside [gutterTop, gutterBottom].
  const spacing = 12;
  const gutterTop = M.t + 6;
  const gutterBottom = H - M.b - 10;
  const capacity = Math.max(1, Math.floor((gutterBottom - gutterTop) / spacing));
  const visibleWalls = walls.length > capacity ? walls.slice(0, capacity - 1) : walls;
  const wallR = compact ? 3 : 4;
  visibleWalls.forEach((c, i) => {
    const y = clampN(gutterBottom - i * spacing, gutterTop, gutterBottom);
    dot(svg, gutterX, y, wallR, COLORS[c.outcome] || "var(--red)", c, false);
  });
  const hiddenWalls = walls.length - visibleWalls.length;
  if (hiddenWalls > 0) {
    const y = clampN(gutterBottom - visibleWalls.length * spacing, gutterTop, gutterBottom);
    const more = document.createElementNS(SVG_NS, "text");
    more.setAttribute("x", gutterX);
    more.setAttribute("y", y);
    more.setAttribute("text-anchor", "middle");
    more.setAttribute("fill", "var(--ink-soft)");
    more.setAttribute("font-size", "10");
    more.setAttribute("class", "gutter-more");
    more.textContent = `+${hiddenWalls} more`;
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${hiddenWalls} more unmeasured candidate(s) killed at a wall before bench`;
    more.appendChild(title);
    svg.appendChild(more);
  }
  if (walls.length) {
    label(svg, gutterX, H - M.b + 16, "walls", "middle");
  }

  // Baseline star.
  if (baseline.latency_ms != null && baseline.peak_mem_bytes != null) {
    const el = document.createElementNS(SVG_NS, "text");
    el.setAttribute("x", sx(baseline.latency_ms));
    el.setAttribute("y", sy(baseline.peak_mem_bytes) + 5);
    el.setAttribute("text-anchor", "middle");
    el.setAttribute("fill", "var(--ink)");
    el.setAttribute("font-size", "16");
    el.textContent = "★";
    svg.appendChild(el);
  }

  // Front polyline (elites shown so far, sorted by latency).
  const elites = measured
    .filter((c) => c.outcome === "pareto_elite")
    .sort((a, b) => a.latency_ms - b.latency_ms);
  if (elites.length > 1) {
    const line = document.createElementNS(SVG_NS, "polyline");
    line.setAttribute("points", elites.map((c) => `${sx(c.latency_ms)},${sy(c.peak_mem_bytes)}`).join(" "));
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "var(--teal)");
    line.setAttribute("stroke-width", "1.5");
    line.setAttribute("stroke-dasharray", "4 3");
    svg.appendChild(line);
  }

  // Measured dots (dominated under elites).
  measured
    .sort((a, b) => (a.outcome === "pareto_elite") - (b.outcome === "pareto_elite"))
    .forEach((c) => {
      const r = clampN(4 + Math.min(6, Math.sqrt((c.diff_bytes || 0) / 12)), 3, compact ? 8 : 10);
      dot(svg, sx(c.latency_ms), sy(c.peak_mem_bytes), r,
          COLORS[c.outcome] || "var(--ink-faint)", c, c.outcome === "pareto_elite");
    });

  const genLabel = $("genLabel");
  if (genLabel) {
    const newCandidates = (p.generations || [])
      .find((generation) => generation.generation === state.shownGen)?.candidates?.length || 0;
    genLabel.textContent = `generation ${state.shownGen}/${state.maxGen} · ${newCandidates ? `${newCandidates} new` : "no new candidates"}`;
  }
}

function pad(lo, hi) {
  if (!isFinite(lo) || !isFinite(hi)) return [0, 1];
  if (lo === hi) return [Math.max(0, lo * 0.95), Math.max(1, hi * 1.05)];
  const span = hi - lo;
  return [Math.max(0, lo - span * 0.08), hi + span * 0.08];
}

function drawAxes(svg, x0, x1, y0, y1, sx, sy, W, H, M, ticks) {
  const axis = (x1p, y1p, x2p, y2p) => {
    const l = document.createElementNS(SVG_NS, "line");
    l.setAttribute("x1", x1p); l.setAttribute("y1", y1p);
    l.setAttribute("x2", x2p); l.setAttribute("y2", y2p);
    l.setAttribute("stroke", "var(--border)");
    svg.appendChild(l);
  };
  axis(M.l, H - M.b, W - M.r, H - M.b);
  axis(M.l, M.t, M.l, H - M.b);
  for (let i = 0; i <= ticks; i++) {
    const xv = x0 + ((x1 - x0) * i) / ticks;
    const yv = y0 + ((y1 - y0) * i) / ticks;
    label(svg, sx(xv), H - M.b + 16, xv.toFixed(2), "middle");
    label(svg, M.l - 8, sy(yv) + 4, humanBytes(yv), "end");
  }
  label(svg, (M.l + W - M.r) / 2, H - 6, "latency_ms (lower is better)", "middle");
  const yl = document.createElementNS(SVG_NS, "text");
  yl.setAttribute("transform", `translate(14 ${(M.t + H - M.b) / 2}) rotate(-90)`);
  yl.setAttribute("text-anchor", "middle");
  yl.setAttribute("fill", "var(--ink-soft)");
  yl.setAttribute("font-size", "11");
  yl.textContent = "peak_mem_bytes (lower is better)";
  svg.appendChild(yl);
}

function label(svg, x, y, text, anchor) {
  const el = document.createElementNS(SVG_NS, "text");
  el.setAttribute("x", x); el.setAttribute("y", y);
  el.setAttribute("text-anchor", anchor);
  el.setAttribute("fill", "var(--ink-soft)");
  el.setAttribute("font-size", "10");
  el.textContent = text;
  svg.appendChild(el);
}

function humanBytes(v) {
  if (v >= 1048576) return `${(v / 1048576).toFixed(1)}M`;
  if (v >= 1024) return `${(v / 1024).toFixed(1)}K`;
  return String(Math.round(v));
}

function dot(svg, x, y, r, color, candidate, elite) {
  const c = document.createElementNS(SVG_NS, "circle");
  c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", r);
  c.setAttribute("fill", color);
  c.setAttribute("fill-opacity", elite ? "0.95" : "0.75");
  c.setAttribute("class", elite ? "dot elite" : "dot");
  c.setAttribute("tabindex", "0");
  c.setAttribute("role", "button");
  const accessibleName = `${candidate.arm} candidate, ${candidate.outcome}`;
  c.setAttribute("aria-label", accessibleName);
  const openReceipt = () => renderDetail(candidate);
  c.addEventListener("click", openReceipt);
  c.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openReceipt();
    }
  });
  const title = document.createElementNS(SVG_NS, "title");
  title.textContent = `${candidate.candidate_id} — ${candidate.arm} — ${candidate.outcome}`;
  c.appendChild(title);
  svg.appendChild(c);
}

// ── Detail panel ─────────────────────────────────────────────────────────────
// Measured-only candidate receipts: fields the stack did not measure read
// "not measured", never an invented number. All values escaped; no raw model
// output is ever assigned as innerHTML.

function renderDetail(candidate) {
  const host = $("detail");
  if (!host) return;
  const p = state.payload;
  const rows = [
    ["candidate", candidate.candidate_id],
    ["arm", candidate.arm],
    ["origin", candidate.origin || "llm"],
    ["parent", candidate.parent_id || "baseline"],
    ["transform", candidate.transform || "n/a"],
    ["outcome", candidate.outcome],
    ["stage reached", candidate.stage],
    ["latency", candidate.latency_ms == null ? "not measured" : `${Number(candidate.latency_ms).toFixed(6)} ms`],
    ["peak memory", candidate.peak_mem_bytes == null ? "not measured" : `${humanBytes(candidate.peak_mem_bytes)} bytes`],
    ["diff_bytes", candidate.diff_bytes ?? "n/a"],
    ["reward", candidate.reward ?? "n/a"],
    ["token cost", fmtUsd(candidate.token_cost_usd)],
  ];
  let html = `<h2>Candidate receipt</h2><span class="outcome-pill">${esc(candidate.outcome)}</span><div class="receipt-grid">` + rows
    .map(([k, v]) => `<span class="muted">${esc(k)}</span><span class="receipt-value">${esc(v)}</span>`)
    .join("") + "</div>";
  if (candidate.arm_receipt) {
    html += `<details class="receipt-json"><summary>Bandit selection receipt</summary>
             <pre>${esc(JSON.stringify(candidate.arm_receipt, null, 2))}</pre></details>`;
  }
  if (candidate.code) {
    const original = p.original_span_stale
      ? "(file changed since the swarm ran — original span unavailable)"
      : (p.original_span || "(unavailable)");
    html += `<div class="muted" style="margin-top:10px">verified code comparison</div>
             <div class="diff-grid"><div><div class="code-label">original span</div><pre>${esc(original)}</pre></div>
             <div><div class="code-label">candidate rewrite</div><pre>${esc(candidate.code)}</pre></div></div>`;
    const terminal = p.status === "completed" || p.status === "cancelled";
    const isPaste = p.input_kind === "paste";
    const applyDisabled = isPaste || state.source !== "live" || p.mode !== "active"
      || !terminal || p.original_span_stale;
    const reason = isPaste
      ? "paste swarms are copy-only"
      : (state.source !== "live"
      ? "apply is disabled for static exports; load the live task"
      : (p.mode !== "active"
        ? "apply is disabled outside UNIGROK_SWARM=active"
        : (!terminal
          ? "apply is disabled while the swarm is still running"
          : (p.original_span_stale ? "file changed since the swarm ran" : ""))));
    const copyLabel = candidate.candidate_id === p.champion_id
      ? "Copy Best Verified Code" : "Copy candidate code";
    html += `<div style="margin-top:8px">
               <button id="copyBtn" class="primary">${esc(copyLabel)}</button>
               <button id="applyBtn" ${applyDisabled ? "disabled" : ""}>Apply optimization</button>
               <span class="muted" style="font-size:11px"> ${esc(reason)}</span>
             </div><div id="applyOut" class="muted" style="margin-top:6px;font-size:12px"></div>`;
  }
  host.innerHTML = html;
  const copyBtn = $("copyBtn");
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      const out = $("applyOut");
      try {
        await navigator.clipboard.writeText(candidate.code);
        if (out) out.textContent = "Best verified code copied exactly.";
      } catch (_error) {
        if (out) out.textContent = "Clipboard access was blocked; select the code above and copy it.";
      }
    });
  }
  const applyBtn = $("applyBtn");
  if (applyBtn && !applyBtn.disabled) {
    applyBtn.addEventListener("click", async () => {
      // Never auto-commit a winner: apply re-runs the tests and reverts the
      // file on failure, and only after an explicit confirmation.
      if (!window.confirm(
        `Apply ${candidate.candidate_id} to ${p.target?.path}? Tests re-run before it lands; the file reverts on failure.`
      )) return;
      applyBtn.disabled = true;
      const out = $("applyOut");
      try {
        const result = await mcpCall("apply_swarm_winner", {
          candidate_id: candidate.candidate_id,
        });
        if (out) out.textContent = result;
      } catch (err) {
        if (out) out.textContent = String(err.message || err);
      }
    });
  }
}

// ── Detail splitter (keyboard-accessible, right-docked, clamped) ─────────────
// Mirrors the Control Center's inspector splitter: the detail pane is right-
// docked, so dragging left grows it. Width is clamped in JS (the CSS max is
// cqw-relative) and written to --swarm-detail on the shell.

function setupDetailSplitter() {
  const splitter = $("detailSplitter");
  const shell = document.querySelector(".swarm-shell");
  if (!splitter || !shell) return;
  const MIN = 280;
  const HARD_MAX = 560;
  const maxFor = () => {
    const shellW = Math.round(shell.getBoundingClientRect().width) || (HARD_MAX * 2);
    return Math.min(HARD_MAX, Math.max(MIN, Math.round(shellW * 0.6)));
  };
  const current = () => {
    const raw = parseInt(getComputedStyle(shell).getPropertyValue("--swarm-detail"), 10);
    return Number.isFinite(raw) ? raw : 360;
  };
  const setWidth = (w) => {
    const width = clampN(Math.round(w), MIN, maxFor());
    shell.style.setProperty("--swarm-detail", `${width}px`);
    splitter.setAttribute("aria-valuenow", String(width));
  };
  splitter.addEventListener("pointerdown", (event) => {
    const startX = event.clientX;
    const startWidth = current();
    splitter.setPointerCapture(event.pointerId);
    splitter.classList.add("dragging");
    const move = (nextEvent) => setWidth(startWidth - (nextEvent.clientX - startX));
    const end = () => {
      splitter.classList.remove("dragging");
      splitter.removeEventListener("pointermove", move);
    };
    splitter.addEventListener("pointermove", move);
    splitter.addEventListener("pointerup", end, { once: true });
    splitter.addEventListener("pointercancel", end, { once: true });
  });
  splitter.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.shiftKey ? 24 : 8;
    // ArrowLeft grows the right-docked pane.
    setWidth(current() + (event.key === "ArrowLeft" ? step : -step));
  });
}

function observeChartResize() {
  const svg = $("chart");
  if (!svg || typeof ResizeObserver === "undefined") return;
  let frame = 0;
  const observer = new ResizeObserver((entries) => {
    if (!state.payload) return;
    const width = Math.round(entries[0]?.contentRect?.width ?? 0);
    if (Math.abs(width - state.lastChartWidth) < 2) return;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      try { renderChart(); } catch (err) { console.error("renderChart failed:", err); }
    });
  });
  observer.observe(svg);
}

// ── Timeline / play ──────────────────────────────────────────────────────────

function stopPlaying() {
  if (state.playing) { clearInterval(state.playing); state.playing = null; }
  const btn = $("playBtn");
  if (btn) btn.textContent = "▶ Play";
}

$("playBtn")?.addEventListener("click", () => {
  if (!state.payload) return;
  if (state.playing) { stopPlaying(); return; }
  state.shownGen = 0;
  const btn = $("playBtn");
  if (btn) btn.textContent = "⏸ Stop";
  state.playing = setInterval(() => {
    state.shownGen = Math.min(state.maxGen, state.shownGen + 1);
    const slider = $("genSlider");
    if (slider) slider.value = String(state.shownGen);
    renderChart();
    if (state.shownGen >= state.maxGen) stopPlaying();
  }, 650);
});

$("genSlider")?.addEventListener("input", (event) => {
  if (!state.payload) return;
  stopPlaying();
  state.shownGen = Number(event.target.value);
  renderChart();
});

$("loadBtn")?.addEventListener("click", loadLive);
$("fileBtn")?.addEventListener("click", () => $("fileInput")?.click());
$("taskId")?.addEventListener("keydown", (e) => { if (e.key === "Enter") loadLive(); });
$("fileInput")?.addEventListener("change", (e) => {
  if (e.target.files && e.target.files[0]) loadFile(e.target.files[0]);
});

// ── On-ramp: sample run, recent-swarm picker, one-click golden demo ──────────
// Three ways to see the instrument without pasting a task id. The sample is a
// RECORDED run (its payload carries a `provenance` field: scripted candidates,
// real measurements); it loads as source "export", so apply stays disabled.

const GOLDEN_DEMO = {
  target_path: "evals/tasks/swarm_targets/nsquared_dedup/dedup.py",
  focus_node: "function:dedup",
  test_target: "evals/tasks/swarm_targets/nsquared_dedup/test_dedup.py",
  bench_command: "python evals/tasks/swarm_targets/nsquared_dedup/bench_dedup.py",
  allow_unstable_bench: true,
};
const TERMINAL_STATUSES = new Set(
  ["completed", "failed", "failed_stale", "cancelled", "stopped_budget"]
);

async function loadSample() {
  setMsg("loading sample…");
  try {
    const res = await fetch(`./swarm-sample.json?v=${encodeURIComponent(UI_ASSET_VERSION)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    if (!STATUS_FORMATS.has(payload.format)) {
      throw new Error("sample is not a supported swarm export");
    }
    setPayload(payload, "sample: recorded golden-dedup run", "export");
    if (payload.provenance) setMsg(payload.provenance);
  } catch (err) {
    setMsg(`sample unavailable: ${err.message || err}`, true);
  }
}

// ── Structured task_id resolution (no regex-scrape of prose) ─────────────────
// A run tool's response may already carry a structured task_id (the companion
// change adds it). If not, we fall back to polling list_swarm_tasks for a
// task_id that did not exist before the run. We NEVER pull a task id out of a
// human-readable sentence, so re-wording a message can't silently break Run.

function isTaskId(candidate) {
  return typeof candidate === "string" && /^[0-9a-f]{8,}$/i.test(candidate.trim());
}

function parseStructuredTaskId(output) {
  if (typeof output !== "string") return null;
  // Preferred: the wording-independent launch receipt the swarm tools append
  // as an HTML-comment trailer (unigrok-swarm-launch-v1). Deterministic parse,
  // never regex over human prose.
  const receipt = output.match(/<!--unigrok-swarm-launch-v1 (\{.*?\})-->/);
  if (receipt) {
    try {
      const payload = JSON.parse(receipt[1]);
      const id = payload && (payload.task_id ?? payload.taskId);
      if (isTaskId(id)) return id.trim();
    } catch (_error) {
      /* fall through to the bare-JSON shape */
    }
  }
  // Fallback: a bare-JSON tool response carrying task_id directly.
  const trimmed = output.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null;
  try {
    const obj = JSON.parse(trimmed);
    const candidate = obj && (obj.task_id ?? obj.taskId);
    if (isTaskId(candidate)) return candidate.trim();
  } catch (_error) {
    return null;
  }
  return null;
}

async function snapshotTaskIds() {
  try {
    const raw = await mcpCall("list_swarm_tasks", { limit: 50 });
    const tasks = JSON.parse(raw);
    return new Set((Array.isArray(tasks) ? tasks : []).map((t) => t.task_id));
  } catch (_error) {
    return new Set();
  }
}

async function resolveTaskId(output, priorIds) {
  const structured = parseStructuredTaskId(output);
  if (structured) return structured;
  // Poll the task list for a run that appeared after we started.
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      const raw = await mcpCall("list_swarm_tasks", { limit: 50 });
      const tasks = JSON.parse(raw);
      const fresh = (Array.isArray(tasks) ? tasks : []).find((t) => t.task_id && !priorIds.has(t.task_id));
      if (fresh) return fresh.task_id;
    } catch (_error) {
      // fall through and retry
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  return null;
}

// ── Paste analysis ──────────────────────────────────────────────────────────

const SLOW_EXAMPLE = `def deduplicate(items):
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
`;

function browserAnalyzePython(code) {
  const lines = code.replace(/\r\n?/g, "\n").split("\n");
  const functions = [];
  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].match(/^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*:/);
    if (!match) continue;
    const indent = match[1].replace(/\t/g, "    ").length;
    let end = index + 1;
    let complexity = 1;
    let maxNesting = 0;
    for (; end < lines.length; end += 1) {
      const line = lines[end];
      if (!line.trim()) continue;
      const currentIndent = (line.match(/^\s*/) || [""])[0].replace(/\t/g, "    ").length;
      if (currentIndent <= indent) break;
      if (/^\s*(if|elif|for|while|except|case)\b/.test(line)) complexity += 1;
      complexity += (line.match(/\b(and|or)\b/g) || []).length;
      maxNesting = Math.max(maxNesting, Math.max(0, Math.floor((currentIndent - indent) / 4)));
    }
    functions.push({
      focus_node: `function:${match[2]}`,
      name: match[2],
      loc: Math.max(1, end - index),
      parameters: match[3].trim() ? match[3].split(",").length : 0,
      cyclomatic_complexity: complexity,
      max_nesting: maxNesting,
      line_start: index + 1,
      line_end: end,
    });
  }
  return {
    format: "unigrok-swarm-analytics-v1",
    parse_ok: functions.length > 0 || code.trim().length === 0,
    bytes: new TextEncoder().encode(code).length,
    loc: lines.filter((line) => line.trim()).length,
    functions,
    searchability: { ready: false, blockers: ["missing_tests", "missing_benchmark"] },
  };
}

function renderAnalysis(result, exact) {
  const badge = $("analysisBadge");
  if (badge) {
    badge.textContent = exact ? "exact local AST" : "client-side preview";
    badge.className = exact ? "source-badge live" : "source-badge";
  }
  const panel = $("analysisResults");
  if (!panel) return;
  if (result.error) {
    panel.innerHTML = `<h3>Could not analyze</h3><div class="err">${esc(result.error)}</div>`;
    return;
  }
  // Only a real parser failure is a parse error. The approximate browser
  // scanner sets parse_ok=false when it merely finds no top-level defs; that
  // must not be misreported as invalid Python.
  if (!result.parse_ok && result.parse_error) {
    const error = result.parse_error;
    panel.innerHTML = `<h3>Fix the parse error first</h3><div class="err">Line ${esc(error.line ?? "?")}: ${esc(error.message || "invalid Python")}</div>`;
    return;
  }
  const rows = (result.functions || []).map((fn) => `
    <div class="function-row">
      <code>${esc(fn.focus_node)}</code>
      <span class="fn-chip">${esc(fn.loc)} LOC</span>
      <span class="fn-chip">CC ${esc(fn.cyclomatic_complexity)}</span>
      <span class="fn-chip">nest ${esc(fn.max_nesting)}</span>
    </div>`).join("");
  const ruffCount = Object.values(result.ruff?.counts_by_code || {})
    .reduce((sum, value) => sum + Number(value || 0), 0);
  panel.innerHTML = `
    <h3>${(result.functions || []).length} function${(result.functions || []).length === 1 ? "" : "s"} found</h3>
    <div class="muted">${esc(result.loc ?? 0)} source lines · ${esc(result.bytes ?? 0)} bytes${exact ? ` · ${ruffCount} Ruff finding${ruffCount === 1 ? "" : "s"}` : " · approximate browser metrics"}</div>
    ${result.secret_warning ? '<div class="err" style="margin-top:7px">Secret-like text detected. Remove it before export or search.</div>' : ""}
    <div class="function-list">${rows || `<div class="muted">${exact ? "No functions found." : "No top-level functions detected by the approximate browser scanner."}</div>`}</div>
    <div class="privacy-note">Search blockers: ${esc((result.searchability?.blockers || []).join(", ") || "none")}</div>`;
  const picker = $("focusPicker");
  if (picker) {
    picker.replaceChildren(new Option(
      (result.functions || []).length ? "choose a function…" : "no functions found", ""
    ));
    for (const fn of result.functions || []) {
      picker.appendChild(new Option(
        `${fn.focus_node} · CC ${fn.cyclomatic_complexity} · ${fn.loc} LOC`, fn.focus_node
      ));
    }
    if ((result.functions || []).length) picker.value = result.functions[0].focus_node;
  }
  const canSearch = exact && state.runtimeMode === "contributor" && (result.functions || []).length > 0;
  const runBtn = $("runPasteBtn");
  if (runBtn) runBtn.disabled = !canSearch;
  const runMsg = $("pasteRunMsg");
  if (runMsg) {
    runMsg.textContent = canSearch
      ? "Tests and benchmark are required; every winner is re-measured."
      : "Verified execution is available only in contributor Forge after exact analysis.";
  }
}

async function analyzePastedCode() {
  const input = $("codeInput");
  const msg = $("analysisMsg");
  const code = input ? input.value : "";
  const size = new TextEncoder().encode(code).length;
  if (!code.trim()) { if (msg) msg.textContent = "paste Python first"; return; }
  if (size > 256 * 1024) { if (msg) msg.textContent = "code exceeds 256 KiB"; return; }
  if (msg) msg.textContent = "analyzing…";
  let result;
  let exact = false;
  // "not uploaded" may only be claimed when no gateway call was attempted;
  // and "browser metrics" only when the browser analyzer actually produced them.
  let submittedToGateway = false;
  let usedBrowserFallback = false;
  if (state.runtimeMode === "contributor") {
    submittedToGateway = true;
    try {
      result = JSON.parse(await mcpCall("analyze_code_for_swarm", { code, language: "python" }));
      exact = !result.error;
    } catch (_error) {
      result = browserAnalyzePython(code);
      usedBrowserFallback = true;
    }
  } else {
    result = browserAnalyzePython(code);
    usedBrowserFallback = true;
  }
  renderAnalysis(result, exact);
  if (!msg) return;
  if (exact) {
    msg.textContent = "analyzed locally without a model call";
  } else if (usedBrowserFallback && submittedToGateway) {
    msg.textContent = "gateway unreachable; showing approximate browser metrics (the paste was submitted to the local gateway)";
  } else if (submittedToGateway) {
    // Gateway returned an error object — the panel shows it; no browser metrics.
    msg.textContent = "gateway analysis failed — see the error above (the paste was submitted to the local gateway)";
  } else {
    msg.textContent = "analyzed in this browser; source was not uploaded";
  }
}

$("analyzeBtn")?.addEventListener("click", analyzePastedCode);
$("pasteExampleBtn")?.addEventListener("click", () => {
  const code = $("codeInput");
  const test = $("testInput");
  const bench = $("benchInput");
  if (code) code.value = SLOW_EXAMPLE;
  if (test) test.value = `from module_under_test import deduplicate

def test_order_and_duplicates():
    assert deduplicate([3, 1, 3, 2, 1]) == [3, 1, 2]
`;
  if (bench) bench.value = `import json
import time
import tracemalloc
from module_under_test import deduplicate

values = list(range(400)) * 3
deduplicate(values[:50])
tracemalloc.start()
started = time.perf_counter()
for _ in range(20):
    deduplicate(values)
latency_ms = (time.perf_counter() - started) * 1000 / 20
_current, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
print("SWARM_BENCH " + json.dumps({"latency_ms": latency_ms, "peak_mem_bytes": peak}))
`;
  analyzePastedCode();
});

async function runPasteSwarm() {
  const button = $("runPasteBtn");
  const runMsg = $("pasteRunMsg");
  const args = {
    code: $("codeInput")?.value ?? "",
    test_code: $("testInput")?.value ?? "",
    bench_code: $("benchInput")?.value ?? "",
    focus_node: $("focusPicker")?.value ?? "",
    search_strategy: $("strategyPicker")?.value ?? "",
    primary_goal: $("goalPicker")?.value ?? "",
  };
  if (!args.focus_node || !args.test_code.trim() || !args.bench_code.trim()) {
    if (runMsg) runMsg.textContent = "Choose a function and provide both tests and benchmark.";
    return;
  }
  if (button) button.disabled = true;
  if (runMsg) runMsg.textContent = "materializing a private local scratch task…";
  try {
    const priorIds = await snapshotTaskIds();
    const output = await mcpCall("start_paste_swarm", args);
    const taskId = await resolveTaskId(output, priorIds);
    if (!taskId) {
      // No structured id and nothing new in the task list: surface the tool's
      // own words verbatim (a gate refusal or an error is instructive).
      if (runMsg) runMsg.textContent = output.replace(/\s+/g, " ").trim();
      return;
    }
    const idField = $("taskId");
    if (idField) idField.value = taskId;
    if (runMsg) runMsg.textContent = "verified swarm running…";
    const final = await pollUntilDone(taskId);
    if (runMsg) {
      if (final && final.status === "completed") {
        runMsg.textContent = "Search complete. Copy Best Verified Code from the receipt.";
      } else if (final) {
        runMsg.textContent = `Swarm ended with status ${final.status} — inspect the receipt before trusting any candidate.`;
      } else {
        runMsg.textContent = "Run did not complete — see the run status message for details.";
      }
    }
    refreshTaskPicker();
  } catch (error) {
    if (runMsg) runMsg.textContent = String(error.message || error);
  } finally {
    if (button) button.disabled = state.runtimeMode !== "contributor";
  }
}

$("runPasteBtn")?.addEventListener("click", runPasteSwarm);

async function refreshTaskPicker() {
  const picker = $("taskPicker");
  if (!picker) return;
  if (state.runtimeMode !== "contributor") {
    picker.replaceChildren(new Option("live runs are available in contributor Forge", ""));
    picker.disabled = true;
    return;
  }
  picker.disabled = false;
  try {
    const raw = await mcpCall("list_swarm_tasks", { limit: 15 });
    const tasks = JSON.parse(raw);
    picker.replaceChildren(new Option(
      tasks.length ? "recent swarms…" : "no swarms on this gateway yet", ""
    ));
    for (const task of tasks) {
      const created = task.created_at ? new Date(task.created_at).toLocaleString([], {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
      }) : "time unknown";
      const label = `${task.status} · ${task.focus_node} · ${created} · ${task.task_id.slice(0, 8)}…`;
      picker.appendChild(new Option(label, task.task_id));
    }
  } catch (err) {
    picker.replaceChildren(new Option("could not read recent swarms — refresh to retry", ""));
  }
}

async function discoverRuntime() {
  const banner = $("runtimeBanner");
  const title = $("runtimeTitle");
  const copy = $("runtimeCopy");
  const forgeNote = $("forgeNote");
  const demoBtn = $("demoBtn");
  const refreshBtn = $("refreshTasksBtn");
  const demoHint = $("demoHint");
  try {
    const response = await fetch("/runtimez", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const runtime = await response.json();
    state.runtimeMode = runtime.service?.mode || "unknown";
    banner?.classList.add("ready");
    if (state.runtimeMode === "contributor" && runtime.service?.workspace_attached) {
      if (title) title.textContent = "Contributor Forge connected";
      if (copy) copy.textContent = "Live task history and real demo runs are available from the attached workspace.";
      if (forgeNote) forgeNote.hidden = true;
      if (demoBtn) demoBtn.disabled = false;
      if (refreshBtn) refreshBtn.disabled = false;
      if (demoHint) demoHint.textContent = "Runs against the attached golden target. If Swarm is off, the exact gate instruction appears below.";
    } else {
      if (title) title.textContent = "Stable gateway connected — exploration mode";
      if (copy) copy.textContent = "The recorded tour works here. Live Swarm tools stay isolated in contributor Forge.";
      if (demoBtn) demoBtn.disabled = true;
      if (refreshBtn) refreshBtn.disabled = true;
      if (demoHint) demoHint.textContent = "Live execution is intentionally unavailable on the workspace-neutral stable service.";
      // Single-origin: reference Forge as a visible-but-locked note only. No
      // live cross-port link is ever emitted from this page.
      if (forgeNote) forgeNote.hidden = false;
    }
  } catch (err) {
    const isLocal = ["127.0.0.1", "localhost", "::1"].includes(window.location.hostname);
    state.runtimeMode = isLocal ? "unknown" : "public";
    if (title) {
      title.textContent = isLocal
        ? "Gateway capability check unavailable"
        : "Public showcase — client-side analysis only";
    }
    if (copy) {
      copy.textContent = isLocal
        ? "The recorded tour still works; live controls may not."
        : "Pasted source stays in this browser. Verified search and Apply require the local contributor Forge.";
    }
    if (demoBtn) demoBtn.disabled = true;
    if (refreshBtn) refreshBtn.disabled = true;
  }
}

async function runGoldenDemo() {
  const btn = $("demoBtn");
  if (btn) btn.disabled = true;
  setMsg("starting demo swarm on the golden O(N²) dedup target…");
  try {
    const priorIds = await snapshotTaskIds();
    const out = await mcpCall("start_code_swarm", GOLDEN_DEMO);
    const taskId = await resolveTaskId(out, priorIds);
    if (!taskId) {
      // Refusals (mode off, stable service, no workspace) are instructive —
      // show the tool's own words verbatim.
      setMsg(out.replace(/\s+/g, " ").trim(), true);
      return;
    }
    const idField = $("taskId");
    if (idField) idField.value = taskId;
    await pollUntilDone(taskId);
    refreshTaskPicker();
  } catch (err) {
    setMsg(String(err.message || err), true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Resolves with the terminal payload, or null when the run errored out or
// polling stopped first — callers must not claim success on null.
async function pollUntilDone(taskId, intervalMs = 3000, maxPolls = 200) {
  for (let i = 0; i < maxPolls; i++) {
    const raw = await mcpCall("get_swarm_status", { task_id: taskId, view: "json" });
    const payload = JSON.parse(raw);
    if (payload.error) { setMsg(payload.error, true); return null; }
    setPayload(payload, "live", "live");
    if (TERMINAL_STATUSES.has(payload.status)) {
      setMsg(
        `run ${payload.status} — ${payload.pareto_front.length} elite(s) on the front`,
        payload.status !== "completed"
      );
      return payload;
    }
    setMsg(`run in progress… (${payload.status}, generation ${payload.budget?.generations_run ?? 0})`);
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  setMsg("stopped polling — the swarm is still running; reload manually.", true);
  return null;
}

$("sampleBtn")?.addEventListener("click", loadSample);
$("demoBtn")?.addEventListener("click", runGoldenDemo);
$("refreshTasksBtn")?.addEventListener("click", refreshTaskPicker);
$("taskPicker")?.addEventListener("change", (event) => {
  if (!event.target.value) return;
  const idField = $("taskId");
  if (idField) idField.value = event.target.value;
  loadLive();
});

async function bootstrap() {
  setupDetailSplitter();
  observeChartResize();
  await loadSample();
  await discoverRuntime();
  await refreshTaskPicker();
}

bootstrap();
