// Swarm Optimizer — Pareto Playground.
// Renders one unigrok-swarm-status-v1 payload (live via get_swarm_status
// view="json", or a static JSON export of the same payload — identical
// rendering is the local/public symmetry). No frameworks, no CDN, no
// simulated data: unmeasured candidates stack in a gutter instead of being
// plotted at invented coordinates.

"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const COLORS = {
  static_wall: "var(--red)",
  test_wall: "var(--orange)",
  dominated: "var(--gray)",
  pareto_elite: "var(--green)",
};

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
      if (payload.format !== "unigrok-swarm-status-v1") {
        setMsg("not a unigrok-swarm-status-v1 export", true);
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
  renderScorecard();
  renderTradeoffSummary();
  renderChart();
  const candidates = (payload.generations || []).flatMap((generation) => generation.candidates || []);
  const leadingFrontId = payload.pareto_front?.[0];
  const firstUseful = candidates.find((candidate) => candidate.candidate_id === leadingFrontId && candidate.code)
    || candidates.find((candidate) => candidate.outcome === "pareto_elite" && candidate.code)
    || candidates.find((candidate) => candidate.code)
    || candidates[0];
  if (firstUseful) renderDetail(firstUseful);
  else $("detail").innerHTML = '<div class="muted">No candidates have landed yet.</div>';
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
    const applyDisabled = state.source !== "live" || p.mode !== "active"
      || !terminal || p.original_span_stale;
    const reason = state.source !== "live"
      ? "apply is disabled for static exports; load the live task"
      : (p.mode !== "active"
        ? "apply is disabled outside UNIGROK_SWARM=active"
        : (!terminal
          ? "apply is disabled while the swarm is still running"
          : (p.original_span_stale ? "file changed since the swarm ran" : "")));
    html += `<div style="margin-top:8px">
               <button id="applyBtn" ${applyDisabled ? "disabled" : ""}>Apply optimization</button>
               <span class="muted" style="font-size:11px"> ${esc(reason)}</span>
             </div><div id="applyOut" class="muted" style="margin-top:6px;font-size:12px"></div>`;
  }
  $("detail").innerHTML = html;
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
    const res = await fetch("./swarm-sample.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    if (payload.format !== "unigrok-swarm-status-v1") {
      throw new Error("sample is not a unigrok-swarm-status-v1 export");
    }
    setPayload(payload, "sample: recorded golden-dedup run", "export");
    if (payload.provenance) setMsg(payload.provenance);
  } catch (err) {
    setMsg(`sample unavailable: ${err.message || err}`, true);
  }
}

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
    state.runtimeMode = "unknown";
    $("runtimeTitle").textContent = "Gateway capability check unavailable";
    $("runtimeCopy").textContent = "The recorded tour still works; live controls may not.";
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

async function pollUntilDone(taskId, intervalMs = 3000, maxPolls = 200) {
  for (let i = 0; i < maxPolls; i++) {
    const raw = await mcpCall("get_swarm_status", { task_id: taskId, view: "json" });
    const payload = JSON.parse(raw);
    if (payload.error) { setMsg(payload.error, true); return; }
    setPayload(payload, "live", "live");
    if (TERMINAL_STATUSES.has(payload.status)) {
      setMsg(`demo ${payload.status} — ${payload.pareto_front.length} elite(s) on the front`);
      return;
    }
    setMsg(`demo running… (${payload.status}, generation ${payload.budget?.generations_run ?? 0})`);
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  setMsg("stopped polling — the swarm is still running; reload manually.", true);
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
