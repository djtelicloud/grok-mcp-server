// Swarm Optimizer — Pareto Playground.
// Renders unigrok-swarm-status-v2 plus recorded v1 exports (live via
// get_swarm_status view="json", or a static JSON export — identical
// rendering is the local/public symmetry). No frameworks, no CDN, no
// simulated data: unmeasured candidates stack in a gutter instead of being
// plotted at invented coordinates.

"use strict";

// Must match src/version.py UI_ASSET_VERSION and the query token on the
// swarm.js reference in swarm.html. The sample uses the same token so a
// revalidated page cannot pair new logic with a heuristically cached payload.
const UI_ASSET_VERSION = "grok-v0.6.0-r7";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const COLORS = {
  static_wall: "var(--red)",
  test_wall: "var(--orange)",
  dominated: "var(--gray)",
  pareto_elite: "var(--green)",
};
const STATUS_FORMATS = new Set(["unigrok-swarm-status-v1", "unigrok-swarm-status-v2"]);

const state = {
  payload: null,
  source: null,
  maxGen: 1,
  shownGen: 1,
  playing: null,
  runtimeMode: "unknown",
};

// ── MCP plumbing (same JSON-RPC shape the Control Center uses) ──────────────

let rpcId = 1;
async function mcpCall(toolName, args) {
  const headers = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "X-Client-ID": "mcp-ui-swarm",
  };
  const token = $("token").value.trim();
  if (token) headers["Authorization"] = `Bearer ${token}`;
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
  const taskId = $("taskId").value.trim();
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
  el.textContent = text;
  el.className = isError ? "err" : "muted";
}

function setPayload(payload, sourceLabel, source) {
  payload = normalizePayload(payload);
  state.payload = payload;
  state.source = source;
  state.maxGen = Math.max(1, ...(payload.generations || []).map((g) => g.generation));
  state.shownGen = state.maxGen;
  $("genSlider").max = String(state.maxGen);
  $("genSlider").value = String(state.maxGen);
  setMsg(`loaded (${sourceLabel})`);
  const badge = $("sourceBadge");
  badge.textContent = source === "live" ? "live gateway data" : sourceLabel;
  badge.className = source === "live" ? "source-badge live" : "source-badge";
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
  if (firstUseful) renderDetail(firstUseful);
  else $("detail").innerHTML = '<div class="muted">No candidates have landed yet.</div>';
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
    ["cost to optimize", `${fmtUsd(agg.cost_to_optimize_usd)} / $${(p.budget?.budget_usd ?? 0).toFixed(2)}`],
    ["focus coverage", oracle.focus_coverage_pct == null ? "n/a" : `${oracle.focus_coverage_pct}%`],
    ["bench", `${bench.stability || "n/a"} (floor ${bench.noise_floor_pct ?? "?"}%)`],
    ["generations", String(p.budget?.generations_run ?? 0)],
  ];
  $("scorecard").innerHTML = cards
    .map(([k, v]) => `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`)
    .join("");
}

function renderTradeoffSummary() {
  const agg = state.payload?.aggregates || {};
  const latency = fmtLatencyImprovement(agg.best_latency_improvement_pct);
  const memory = fmtMemoryImprovement(agg.best_memory_improvement_pct);
  $("tradeoffSummary").textContent = latency === "n/a"
    ? "No benchmark-qualified candidate is available yet."
    : `Pareto readout: the fastest verified candidate is ${latency} and uses ${memory} peak memory. Neither axis is hidden.`;
}

function esc(value) {
  return String(value).replace(/[&<>"]/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}

// ── Chart ────────────────────────────────────────────────────────────────────

const M = { l: 70, r: 20, t: 16, b: 42 };
const W = 860, H = 420;
const GUTTER_X = 34; // unmeasured candidates stack here, outside the axes

function candidatesUpTo(gen) {
  return (state.payload.generations || [])
    .filter((g) => g.generation <= gen)
    .flatMap((g) => g.candidates);
}

function renderChart() {
  const svg = $("chart");
  svg.innerHTML = "";
  const p = state.payload;
  const shown = candidatesUpTo(state.shownGen);
  const measured = shown.filter((c) => c.feasible && c.latency_ms != null);
  const walls = shown.filter((c) => !(c.feasible && c.latency_ms != null));
  const baseline = p.baseline || {};

  const xs = measured.map((c) => c.latency_ms).concat(baseline.latency_ms ?? []);
  const ys = measured.map((c) => c.peak_mem_bytes).concat(baseline.peak_mem_bytes ?? []);
  const [x0, x1] = pad(Math.min(...xs, Infinity), Math.max(...xs, -Infinity));
  const [y0, y1] = pad(Math.min(...ys, Infinity), Math.max(...ys, -Infinity));
  const sx = (v) => M.l + ((v - x0) / (x1 - x0)) * (W - M.l - M.r);
  const sy = (v) => H - M.b - ((v - y0) / (y1 - y0)) * (H - M.t - M.b);

  drawAxes(svg, x0, x1, y0, y1, sx, sy);

  // Gutter stack: killed-before-measurement candidates, deterministic order.
  walls.forEach((c, i) => {
    dot(svg, GUTTER_X, H - M.b - 10 - i * 12, 4, COLORS[c.outcome] || "var(--red)", c, false);
  });
  if (walls.length) {
    label(svg, GUTTER_X, H - M.b + 16, "walls", "middle");
  }

  // Baseline star.
  if (baseline.latency_ms != null && baseline.peak_mem_bytes != null) {
    const el = document.createElementNS(SVG_NS, "text");
    el.setAttribute("x", sx(baseline.latency_ms));
    el.setAttribute("y", sy(baseline.peak_mem_bytes) + 5);
    el.setAttribute("text-anchor", "middle");
    el.setAttribute("fill", "var(--text)");
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
    line.setAttribute("stroke", "var(--green)");
    line.setAttribute("stroke-width", "1.5");
    line.setAttribute("stroke-dasharray", "4 3");
    svg.appendChild(line);
  }

  // Measured dots (dominated under elites).
  measured
    .sort((a, b) => (a.outcome === "pareto_elite") - (b.outcome === "pareto_elite"))
    .forEach((c) => {
      const r = 4 + Math.min(6, Math.sqrt((c.diff_bytes || 0) / 12));
      dot(svg, sx(c.latency_ms), sy(c.peak_mem_bytes), r,
          COLORS[c.outcome] || "var(--gray)", c, c.outcome === "pareto_elite");
    });

  const newCandidates = (p.generations || [])
    .find((generation) => generation.generation === state.shownGen)?.candidates?.length || 0;
  $("genLabel").textContent = `generation ${state.shownGen}/${state.maxGen} · ${newCandidates ? `${newCandidates} new` : "no new candidates"}`;
}

function pad(lo, hi) {
  if (!isFinite(lo) || !isFinite(hi)) return [0, 1];
  if (lo === hi) return [Math.max(0, lo * 0.95), Math.max(1, hi * 1.05)];
  const span = hi - lo;
  return [Math.max(0, lo - span * 0.08), hi + span * 0.08];
}

function drawAxes(svg, x0, x1, y0, y1, sx, sy) {
  const axis = (x1p, y1p, x2p, y2p) => {
    const l = document.createElementNS(SVG_NS, "line");
    l.setAttribute("x1", x1p); l.setAttribute("y1", y1p);
    l.setAttribute("x2", x2p); l.setAttribute("y2", y2p);
    l.setAttribute("stroke", "var(--border)");
    svg.appendChild(l);
  };
  axis(M.l, H - M.b, W - M.r, H - M.b);
  axis(M.l, M.t, M.l, H - M.b);
  for (let i = 0; i <= 4; i++) {
    const xv = x0 + ((x1 - x0) * i) / 4;
    const yv = y0 + ((y1 - y0) * i) / 4;
    label(svg, sx(xv), H - M.b + 16, xv.toFixed(2), "middle");
    label(svg, M.l - 8, sy(yv) + 4, humanBytes(yv), "end");
  }
  label(svg, (M.l + W - M.r) / 2, H - 6, "latency_ms (lower is better)", "middle");
  const yl = document.createElementNS(SVG_NS, "text");
  yl.setAttribute("transform", `translate(14 ${(M.t + H - M.b) / 2}) rotate(-90)`);
  yl.setAttribute("text-anchor", "middle");
  yl.setAttribute("fill", "var(--muted)");
  yl.setAttribute("font-size", "11");
  yl.textContent = "peak_mem_bytes (lower is better)";
  svg.appendChild(yl);
}

function label(svg, x, y, text, anchor) {
  const el = document.createElementNS(SVG_NS, "text");
  el.setAttribute("x", x); el.setAttribute("y", y);
  el.setAttribute("text-anchor", anchor);
  el.setAttribute("fill", "var(--muted)");
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

function renderDetail(candidate) {
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
  $("detail").innerHTML = html;
  const copyBtn = $("copyBtn");
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(candidate.code);
        $("applyOut").textContent = "Best verified code copied exactly.";
      } catch (_error) {
        $("applyOut").textContent = "Clipboard access was blocked; select the code above and copy it.";
      }
    });
  }
  const applyBtn = $("applyBtn");
  if (applyBtn && !applyBtn.disabled) {
    applyBtn.addEventListener("click", async () => {
      if (!window.confirm(
        `Apply ${candidate.candidate_id} to ${p.target?.path}? Tests re-run before it lands; the file reverts on failure.`
      )) return;
      applyBtn.disabled = true;
      try {
        $("applyOut").textContent = await mcpCall("apply_swarm_winner", {
          candidate_id: candidate.candidate_id,
        });
      } catch (err) {
        $("applyOut").textContent = String(err.message || err);
      }
    });
  }
}

// ── Timeline / play ──────────────────────────────────────────────────────────

function stopPlaying() {
  if (state.playing) { clearInterval(state.playing); state.playing = null; }
  $("playBtn").textContent = "▶ Play";
}

$("playBtn").addEventListener("click", () => {
  if (!state.payload) return;
  if (state.playing) { stopPlaying(); return; }
  state.shownGen = 0;
  $("playBtn").textContent = "⏸ Stop";
  state.playing = setInterval(() => {
    state.shownGen = Math.min(state.maxGen, state.shownGen + 1);
    $("genSlider").value = String(state.shownGen);
    renderChart();
    if (state.shownGen >= state.maxGen) stopPlaying();
  }, 650);
});

$("genSlider").addEventListener("input", (event) => {
  if (!state.payload) return;
  stopPlaying();
  state.shownGen = Number(event.target.value);
  renderChart();
});

$("loadBtn").addEventListener("click", loadLive);
$("fileBtn").addEventListener("click", () => $("fileInput").click());
$("taskId").addEventListener("keydown", (e) => { if (e.key === "Enter") loadLive(); });
$("fileInput").addEventListener("change", (e) => {
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
  badge.textContent = exact ? "exact local AST" : "client-side preview";
  badge.className = exact ? "source-badge live" : "source-badge";
  if (result.error) {
    $("analysisResults").innerHTML = `<h3>Could not analyze</h3><div class="err">${esc(result.error)}</div>`;
    return;
  }
  // Only a real parser failure is a parse error. The approximate browser
  // scanner sets parse_ok=false when it merely finds no top-level defs; that
  // must not be misreported as invalid Python.
  if (!result.parse_ok && result.parse_error) {
    const error = result.parse_error;
    $("analysisResults").innerHTML = `<h3>Fix the parse error first</h3><div class="err">Line ${esc(error.line ?? "?")}: ${esc(error.message || "invalid Python")}</div>`;
    return;
  }
  const rows = (result.functions || []).map((fn) => `
    <div class="function-row">
      <code>${esc(fn.focus_node)}</code>
      <span class="metric-chip">${esc(fn.loc)} LOC</span>
      <span class="metric-chip">CC ${esc(fn.cyclomatic_complexity)}</span>
      <span class="metric-chip">nest ${esc(fn.max_nesting)}</span>
    </div>`).join("");
  const ruffCount = Object.values(result.ruff?.counts_by_code || {})
    .reduce((sum, value) => sum + Number(value || 0), 0);
  $("analysisResults").innerHTML = `
    <h3>${(result.functions || []).length} function${(result.functions || []).length === 1 ? "" : "s"} found</h3>
    <div class="muted">${esc(result.loc ?? 0)} source lines · ${esc(result.bytes ?? 0)} bytes${exact ? ` · ${ruffCount} Ruff finding${ruffCount === 1 ? "" : "s"}` : " · approximate browser metrics"}</div>
    ${result.secret_warning ? '<div class="err" style="margin-top:7px">Secret-like text detected. Remove it before export or search.</div>' : ""}
    <div class="function-list">${rows || `<div class="muted">${exact ? "No functions found." : "No top-level functions detected by the approximate browser scanner."}</div>`}</div>
    <div class="privacy-note">Search blockers: ${esc((result.searchability?.blockers || []).join(", ") || "none")}</div>`;
  const picker = $("focusPicker");
  picker.replaceChildren(new Option(
    (result.functions || []).length ? "choose a function…" : "no functions found", ""
  ));
  for (const fn of result.functions || []) {
    picker.appendChild(new Option(
      `${fn.focus_node} · CC ${fn.cyclomatic_complexity} · ${fn.loc} LOC`, fn.focus_node
    ));
  }
  if ((result.functions || []).length) picker.value = result.functions[0].focus_node;
  const canSearch = exact && state.runtimeMode === "contributor" && (result.functions || []).length > 0;
  $("runPasteBtn").disabled = !canSearch;
  $("pasteRunMsg").textContent = canSearch
    ? "Tests and benchmark are required; every winner is re-measured."
    : "Verified execution is available only in contributor Forge after exact analysis.";
}

async function analyzePastedCode() {
  const code = $("codeInput").value;
  const size = new TextEncoder().encode(code).length;
  if (!code.trim()) { $("analysisMsg").textContent = "paste Python first"; return; }
  if (size > 256 * 1024) { $("analysisMsg").textContent = "code exceeds 256 KiB"; return; }
  $("analysisMsg").textContent = "analyzing…";
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
  if (exact) {
    $("analysisMsg").textContent = "analyzed locally without a model call";
  } else if (usedBrowserFallback && submittedToGateway) {
    $("analysisMsg").textContent = "gateway unreachable; showing approximate browser metrics (the paste was submitted to the local gateway)";
  } else if (submittedToGateway) {
    // Gateway returned an error object — the panel shows it; no browser metrics.
    $("analysisMsg").textContent = "gateway analysis failed — see the error above (the paste was submitted to the local gateway)";
  } else {
    $("analysisMsg").textContent = "analyzed in this browser; source was not uploaded";
  }
}

$("analyzeBtn").addEventListener("click", analyzePastedCode);
$("pasteExampleBtn").addEventListener("click", () => {
  $("codeInput").value = SLOW_EXAMPLE;
  $("testInput").value = `from module_under_test import deduplicate

def test_order_and_duplicates():
    assert deduplicate([3, 1, 3, 2, 1]) == [3, 1, 2]
`;
  $("benchInput").value = `import json
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
  const args = {
    code: $("codeInput").value,
    test_code: $("testInput").value,
    bench_code: $("benchInput").value,
    focus_node: $("focusPicker").value,
    search_strategy: $("strategyPicker").value,
    primary_goal: $("goalPicker").value,
  };
  if (!args.focus_node || !args.test_code.trim() || !args.bench_code.trim()) {
    $("pasteRunMsg").textContent = "Choose a function and provide both tests and benchmark.";
    return;
  }
  button.disabled = true;
  $("pasteRunMsg").textContent = "materializing a private local scratch task…";
  try {
    const output = await mcpCall("start_paste_swarm", args);
    const match = output.match(/`([0-9a-f]{16,})`/);
    if (!match) { $("pasteRunMsg").textContent = output.replace(/\s+/g, " ").trim(); return; }
    $("taskId").value = match[1];
    $("pasteRunMsg").textContent = "verified swarm running…";
    const final = await pollUntilDone(match[1]);
    if (final && final.status === "completed") {
      $("pasteRunMsg").textContent = "Search complete. Copy Best Verified Code from the receipt.";
    } else if (final) {
      $("pasteRunMsg").textContent = `Swarm ended with status ${final.status} — inspect the receipt before trusting any candidate.`;
    } else {
      $("pasteRunMsg").textContent = "Run did not complete — see the run status message for details.";
    }
    refreshTaskPicker();
  } catch (error) {
    $("pasteRunMsg").textContent = String(error.message || error);
  } finally {
    button.disabled = state.runtimeMode !== "contributor";
  }
}

$("runPasteBtn").addEventListener("click", runPasteSwarm);

async function refreshTaskPicker() {
  const picker = $("taskPicker");
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
  try {
    const response = await fetch("/runtimez", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const runtime = await response.json();
    state.runtimeMode = runtime.service?.mode || "unknown";
    banner.classList.add("ready");
    if (state.runtimeMode === "contributor" && runtime.service?.workspace_attached) {
      $("runtimeTitle").textContent = "Contributor Forge connected";
      $("runtimeCopy").textContent = "Live task history and real demo runs are available from the attached workspace.";
      $("forgeLink").hidden = true;
      $("demoBtn").disabled = false;
      $("refreshTasksBtn").disabled = false;
      $("demoHint").innerHTML = "Runs against the attached golden target. If Swarm is off, the exact gate instruction appears below.";
    } else {
      $("runtimeTitle").textContent = "Stable gateway connected — exploration mode";
      $("runtimeCopy").textContent = "The recorded tour works here. Live Swarm tools stay isolated in contributor Forge on port 4766.";
      $("demoBtn").disabled = true;
      $("refreshTasksBtn").disabled = true;
      $("demoHint").textContent = "Live execution is intentionally unavailable on the workspace-neutral stable service.";
      if (["127.0.0.1", "localhost"].includes(window.location.hostname)) {
        $("forgeLink").href = `${window.location.protocol}//${window.location.hostname}:4766/ui/swarm.html`;
        $("forgeLink").hidden = false;
      }
    }
  } catch (err) {
    const isLocal = ["127.0.0.1", "localhost", "::1"].includes(window.location.hostname);
    state.runtimeMode = isLocal ? "unknown" : "public";
    $("runtimeTitle").textContent = isLocal
      ? "Gateway capability check unavailable"
      : "Public showcase — client-side analysis only";
    $("runtimeCopy").textContent = isLocal
      ? "The recorded tour still works; live controls may not."
      : "Pasted source stays in this browser. Verified search and Apply require the local contributor Forge.";
    $("demoBtn").disabled = true;
    $("refreshTasksBtn").disabled = true;
  }
}

async function runGoldenDemo() {
  const btn = $("demoBtn");
  btn.disabled = true;
  setMsg("starting demo swarm on the golden O(N²) dedup target…");
  try {
    const out = await mcpCall("start_code_swarm", GOLDEN_DEMO);
    const match = out.match(/`([0-9a-f]{16,})`/);
    if (!match) {
      // Refusals (mode off, stable service, no workspace) are instructive —
      // show the tool's own words verbatim.
      setMsg(out.replace(/\s+/g, " ").trim(), true);
      return;
    }
    const taskId = match[1];
    $("taskId").value = taskId;
    await pollUntilDone(taskId);
    refreshTaskPicker();
  } catch (err) {
    setMsg(String(err.message || err), true);
  } finally {
    btn.disabled = false;
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

$("sampleBtn").addEventListener("click", loadSample);
$("demoBtn").addEventListener("click", runGoldenDemo);
$("refreshTasksBtn").addEventListener("click", refreshTaskPicker);
$("taskPicker").addEventListener("change", (event) => {
  if (!event.target.value) return;
  $("taskId").value = event.target.value;
  loadLive();
});

async function bootstrap() {
  await loadSample();
  await discoverRuntime();
  await refreshTaskPicker();
}

bootstrap();
