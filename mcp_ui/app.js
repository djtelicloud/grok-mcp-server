import { parseMarkdown, sanitizeHref } from "./markdown.js?v=grok-v0.6.0-r12";

// Must match the <meta name="unigrok-ui-version"> baked into index.html and
// src/version.py UI_ASSET_VERSION; a mismatch means the browser paired a
// cached page with a different script build (the stale-skew failure class).
const UI_ASSET_VERSION = "grok-v0.6.0-r12";

const LAYOUT_KEY = "unigrok.mcp.console.layout.v2";

// Panel bounds are authored once in styles.css (:root --nav-min/max,
// --inspector-min/max); the JS fallbacks only cover a stylesheet that failed
// to load, where layout precision no longer matters.
function cssPx(name, fallback) {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name);
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) ? value : fallback;
}

const LAYOUT_LIMITS = {
  nav: [cssPx("--nav-min", 148), cssPx("--nav-max", 340)],
  inspector: [cssPx("--inspector-min", 210), cssPx("--inspector-max", 460)],
  workbench: cssPx("--workbench-min", 300),
  workbenchComfort: 360,
  rail: cssPx("--rail-width", 44),
  splitter: cssPx("--splitter-width", 4),
};

const defaultLayout = {
  navPresence: "show",
  inspectorPresence: "hide",
  navWidth: 168,
  inspectorWidth: 280,
  density: "auto",
};

const TAB_IDS = new Set([
  "tab-onboarding",
  "tab-metrics",
  "tab-models",
]);

function readLayout() {
  try {
    const current = localStorage.getItem(LAYOUT_KEY);
    const saved = JSON.parse(current || localStorage.getItem("unigrok.mcp.console.layout.v1") || "{}");
    const legacyNav = saved.nav === "hidden" ? "hide" : "show";
    // v1 persisted resolver outcomes, not trustworthy user intent. Preserve a
    // hidden nav choice, but retire its default-open inspector rail.
    const legacyInspector = "hide";
    return {
      ...defaultLayout,
      ...saved,
      navPresence: ["show", "hide"].includes(saved.navPresence) ? saved.navPresence : legacyNav,
      inspectorPresence: ["show", "hide"].includes(saved.inspectorPresence) ? saved.inspectorPresence : legacyInspector,
      navWidth: clamp(Number(saved.navWidth) || defaultLayout.navWidth, ...LAYOUT_LIMITS.nav),
      inspectorWidth: clamp(Number(saved.inspectorWidth) || defaultLayout.inspectorWidth, ...LAYOUT_LIMITS.inspector),
      density: ["auto", "compact", "comfortable"].includes(saved.density) ? saved.density : "auto",
    };
  } catch (_) {
    return { ...defaultLayout };
  }
}

let layoutState = readLayout();
const layoutSession = { navDrawerOpen: false, inspectorDrawerOpen: false, resizing: false };
let layoutAnimationTimer = null;
let layoutResizeFrame = 0;
let layoutResizeTimer = 0;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function resolveLayout(width, height, intent = layoutState) {
  const navDockCost = intent.navWidth + LAYOUT_LIMITS.splitter;
  const inspectorDockCost = intent.inspectorWidth + LAYOUT_LIMITS.splitter;
  let nav = intent.navPresence === "show" ? "dock" : "hidden";
  let inspector = intent.inspectorPresence === "show" ? "dock" : "hidden";

  if (nav === "dock" && width < navDockCost + LAYOUT_LIMITS.workbenchComfort) nav = "rail";
  if (nav === "rail" && width < LAYOUT_LIMITS.rail + LAYOUT_LIMITS.workbench) nav = "drawer";
  const navCost = nav === "dock" ? navDockCost : nav === "rail" ? LAYOUT_LIMITS.rail + LAYOUT_LIMITS.splitter : 0;
  if (inspector === "dock" && width < navCost + inspectorDockCost + LAYOUT_LIMITS.workbenchComfort) inspector = "drawer";

  const density = intent.density === "auto"
    ? (height < 640 || width < 520 ? "compact" : "comfortable")
    : intent.density;
  return { nav, inspector, density, stickyActions: height < 700 || density === "compact" };
}

function saveLayout() {
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layoutState)); } catch (_) { /* session-only fallback */ }
}

function applyLayout({ animate = false } = {}) {
  const grid = $("consoleGrid");
  if (!grid) return;
  const rect = grid.getBoundingClientRect();
  const effective = resolveLayout(rect.width || window.innerWidth, rect.height || window.innerHeight);

  grid.dataset.nav = effective.nav;
  grid.dataset.inspector = effective.inspector;
  grid.dataset.navDrawer = layoutSession.navDrawerOpen ? "open" : "closed";
  grid.dataset.inspectorDrawer = layoutSession.inspectorDrawerOpen ? "open" : "closed";
  grid.dataset.density = effective.density;
  grid.dataset.stickyActions = String(effective.stickyActions);
  grid.dataset.resizing = String(layoutSession.resizing);
  grid.style.setProperty("--nav-width", `${layoutState.navWidth}px`);
  grid.style.setProperty("--inspector-width", `${layoutState.inspectorWidth}px`);
  grid.dataset.layout = JSON.stringify({
    nav: effective.nav,
    inspector: effective.inspector,
    navWidth: layoutState.navWidth,
    inspectorWidth: layoutState.inspectorWidth,
    density: effective.density,
    stickyActions: effective.stickyActions,
  });

  $("toggleNavBtn")?.setAttribute("aria-pressed", String(layoutState.navPresence === "show"));
  $("toggleNavBtn")?.setAttribute("aria-expanded", String(effective.nav !== "hidden" && (effective.nav !== "drawer" || layoutSession.navDrawerOpen)));
  $("toggleInspectorBtn")?.setAttribute("aria-pressed", String(layoutState.inspectorPresence === "show"));
  $("toggleInspectorBtn")?.setAttribute("aria-expanded", String(effective.inspector !== "hidden" && (effective.inspector !== "drawer" || layoutSession.inspectorDrawerOpen)));
  $("densityBtn")?.setAttribute("aria-pressed", String(layoutState.density === "compact"));
  $("navSplitter")?.setAttribute("aria-valuenow", String(layoutState.navWidth));
  $("inspectorSplitter")?.setAttribute("aria-valuenow", String(layoutState.inspectorWidth));

  if (animate) {
    grid.classList.add("layout-animate");
    clearTimeout(layoutAnimationTimer);
    layoutAnimationTimer = setTimeout(() => grid.classList.remove("layout-animate"), 160);
  }
}

function togglePanel(side) {
  const key = `${side}Presence`;
  const showing = layoutState[key] === "show";
  layoutState[key] = showing ? "hide" : "show";
  const grid = $("consoleGrid");
  const rect = grid?.getBoundingClientRect() || { width: window.innerWidth, height: window.innerHeight };
  const resolved = resolveLayout(rect.width, rect.height);
  layoutSession[`${side}DrawerOpen`] = !showing && resolved[side] === "drawer";
  saveLayout();
  applyLayout({ animate: true });
}

function bindSplitter(id, side) {
  const splitter = $(id);
  if (!splitter) return;
  const widthKey = side === "nav" ? "navWidth" : "inspectorWidth";
  const limits = LAYOUT_LIMITS[side];

  const resizeTo = (delta, startWidth) => {
    const direction = side === "nav" ? 1 : -1;
    layoutState[widthKey] = clamp(Math.round(startWidth + delta * direction), ...limits);
    layoutState[`${side}Presence`] = "show";
    saveLayout();
    applyLayout();
  };

  splitter.addEventListener("pointerdown", (event) => {
    const startX = event.clientX;
    const startWidth = layoutState[widthKey];
    splitter.setPointerCapture(event.pointerId);
    splitter.classList.add("dragging");
    const move = (nextEvent) => resizeTo(nextEvent.clientX - startX, startWidth);
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
    const physicalDelta = event.key === "ArrowRight" ? step : -step;
    resizeTo(physicalDelta, layoutState[widthKey]);
  });
}

function setupLayoutController() {
  applyLayout();
  $("toggleNavBtn")?.addEventListener("click", () => togglePanel("nav"));
  $("toggleInspectorBtn")?.addEventListener("click", () => togglePanel("inspector"));
  $("densityBtn")?.addEventListener("click", () => {
    layoutState.density = layoutState.density === "compact" ? "comfortable" : "compact";
    saveLayout();
    applyLayout({ animate: true });
  });
  bindSplitter("navSplitter", "nav");
  bindSplitter("inspectorSplitter", "inspector");

  document.addEventListener("keydown", (event) => {
    const command = event.ctrlKey || event.metaKey;
    if (command && !event.shiftKey && event.key.toLowerCase() === "b") {
      event.preventDefault();
      togglePanel("nav");
    } else if (command && event.shiftKey && event.key.toLowerCase() === "i") {
      event.preventDefault();
      togglePanel("inspector");
    } else if (event.key === "Escape" && (layoutSession.navDrawerOpen || layoutSession.inspectorDrawerOpen)) {
      layoutSession.navDrawerOpen = false;
      layoutSession.inspectorDrawerOpen = false;
      applyLayout({ animate: true });
    }
  });

  const observer = new ResizeObserver(() => {
    layoutSession.resizing = true;
    cancelAnimationFrame(layoutResizeFrame);
    layoutResizeFrame = requestAnimationFrame(() => applyLayout());
    clearTimeout(layoutResizeTimer);
    layoutResizeTimer = setTimeout(() => {
      layoutSession.resizing = false;
      applyLayout();
    }, 140);
  });
  observer.observe($("consoleGrid"));
}

const state = {
  activeTab: "tab-onboarding",
  activeSchema: "AgentResult",
  activeOkfFile: "index.md",
  okfManifest: null,
  models: [],
  busy: false,
  requestIdCounter: 1,
  clientToken: "",
  metricsPeriod: "today",
  metricsSnapshot: null,
  modelCatalog: null,
  modelCatalogLoading: false,
  modelCatalogGeneration: 0,
  credentialPlanes: null,
};

// Only static/fallback model lists — not operational unavailable/skipped sources.
const FALLBACK_CATALOG_SOURCES = new Set([
  "cli-fallback",
  "xai_api_fallback",
]);

// --- DOM Selector Helper ---
const $ = (id) => document.getElementById(id);

// Null-safe text writer. A stale-cached page can lack elements this build
// writes to; a missing diagnostics row must never throw inside the response
// path and discard a paid agent answer.
function setText(id, value) {
  const el = $(id);
  if (el) el.innerText = value;
  return el;
}

// --- CSS Tooltip/Copy Helper ---
function copyTextToClipboard(text, btnElement) {
  navigator.clipboard.writeText(text).then(() => {
    const originalText = btnElement.innerText;
    btnElement.innerText = "Copied!";
    btnElement.style.background = "var(--teal-soft)";
    btnElement.style.color = "var(--teal)";
    setTimeout(() => {
      btnElement.innerText = originalText;
      btnElement.style.background = "";
      btnElement.style.color = "";
    }, 1500);
  }).catch((err) => {
    console.error("Clipboard copy failed: ", err);
  });
}

// --- Dynamic Tab Router ---
function setupTabRouter() {
  const tablist = $("sidebarNav");
  const tabs = [...document.querySelectorAll(".nav-btn")];
  tabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tabId = btn.getAttribute("data-tab");
      switchTab(tabId);
    });
  });
  tablist?.addEventListener("keydown", (event) => {
    const visibleTabs = tabs.filter((tab) => tab.offsetParent !== null);
    const index = visibleTabs.indexOf(document.activeElement);
    if (index < 0) return;
    let next = index;
    if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % visibleTabs.length;
    else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index - 1 + visibleTabs.length) % visibleTabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = visibleTabs.length - 1;
    else return;
    event.preventDefault();
    visibleTabs[next].focus();
    visibleTabs[next].click();
  });
}

function switchTab(tabId) {
  if (!TAB_IDS.has(tabId)) return;
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.classList.remove("active");
    b.setAttribute("aria-selected", "false");
    b.tabIndex = -1;
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.classList.remove("active");
    p.hidden = true;
  });

  const targetBtn = Array.from(document.querySelectorAll(".nav-btn[data-tab]"))
    .find((button) => button.dataset.tab === tabId);
  const targetPanel = $(tabId);

  if (targetBtn && targetPanel) {
    if (targetBtn.closest("#advancedNav")) $("advancedNav").open = true;
    targetBtn.classList.add("active");
    targetBtn.setAttribute("aria-selected", "true");
    targetBtn.tabIndex = 0;
    targetPanel.classList.add("active");
    targetPanel.hidden = false;
    state.activeTab = tabId;

    // Trigger tab-specific initializations
    if (tabId === "tab-schemas") {
      renderActiveSchema();
    } else if (tabId === "tab-okf" && !state.okfManifest) {
      loadOkfManifest();
    } else if (tabId === "tab-webmcp") {
      loadWebMcpManifest();
      checkWebMcpBridge();
    } else if (tabId === "tab-models") {
      // Lazy-load expensive dual-plane discovery only when Planes is opened.
      loadPlaneModelCatalog(false);
    } else if (tabId === "tab-metrics") {
      fetchLiveMetrics();
    }
  }
}

// --- Illustrative result shapes (not wire schemas) ---
// Live MCP tools/list output is authoritative. These compact examples exist
// only to make common response fields easier to browse in the console.
const resultShapeExamples = {
  AgentResult: {
    description: "Illustrative fields commonly returned by unified agent actions.",
    properties: {
      response: { type: "string", description: "Core generated text content." },
      text: { type: "string", description: "Formatted markdown string with citation footers." },
      finish_reason: { type: "string", enum: ["final_answer", "fallback", "tool_calls", "length", "unknown", "error"] },
      cost_usd: { type: "number", description: "Aggregated execution cost in USD." },
      model: { type: "string", description: "ID of the Grok model serving the call." },
      profile: { type: "string", description: "Adapter hyperparams profile name." },
      tokens: { type: "integer", description: "Aggregated tokens consumed." },
      latency_sec: { type: "number", description: "Total request round-trip time." },
      route: { type: "string", enum: ["fast", "agentic", "thinking", "research", "cli-fallback", "utility"] },
      plane: { type: "string", enum: ["API", "CLI", "CLI-Fallback", "local", "utility"] },
      citations: { type: "array", items: { type: "object", properties: { url: { type: "string" } } } },
      why: { type: "string", description: "Decision rationale metric." },
      routing: { type: "object", description: "Versioned prompt-free model-selection receipt." },
      degraded: { type: "boolean", description: "True if model fallback triggered." },
      trace: { type: "array", description: "Structured multi-step execution trace." }
    },
  },
  ChatResult: {
    description: "Illustrative fields commonly returned by chat completions.",
    properties: {
      response: { type: "string" },
      text: { type: "string" },
      finish_reason: { type: "string" },
      cost_usd: { type: "number" },
      model: { type: "string" },
      tokens: { type: "integer" },
      latency_sec: { type: "number" },
      response_id: { type: "string", description: "xAI upstream conversation turn identifier." },
      session: { type: "string", description: "Local persistent session name." }
    },
  },
  ReflectionResult: {
    description: "Illustrative critique fields returned by grok_reflect.",
    properties: {
      ok: { type: "boolean", description: "True if reflection succeeded." },
      critique: {
        type: "object",
        properties: {
          verdict: { type: "string", enum: ["pass", "needs_changes"] },
          summary: { type: "string" },
          strengths: { type: "array", items: { type: "string" } },
          issues: { type: "array", items: { type: "string" } },
          recommendations: { type: "array", items: { type: "string" } },
          confidence: { type: "number" }
        }
      }
    },
  },
  MediaResult: {
    description: "Illustrative Grok Imagine image and video result fields.",
    properties: {
      response: { type: "string" },
      text: { type: "string" },
      images: { type: "array", items: { type: "string", description: "Generated image URLs." } },
      video_url: { type: "string" },
      duration_sec: { type: "number" },
      imagine_params: { type: "object", description: "Prompt parameters context for reproducibility." }
    },
  },
  SystemResult: {
    description: "Payload format for search execution and system checks.",
    properties: {
      response: { type: "string" },
      text: { type: "string" },
      data: { type: "object", description: "Raw structured JSON outputs (citations, environment variables)." }
    },
  }
};

function setupSchemaExplorer() {
  document.querySelectorAll(".schema-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".schema-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.activeSchema = btn.getAttribute("data-schema");
      renderActiveSchema();
    });
  });

  $("copySchemaBtn").addEventListener("click", function() {
    const code = $("schemaCodeBlock").innerText;
    copyTextToClipboard(code, this);
  });
}

function renderActiveSchema() {
  const schema = resultShapeExamples[state.activeSchema];
  setText("schemaNameTitle", `${state.activeSchema}.example.json`);
  setText("schemaCodeBlock", schema ? JSON.stringify({ authoritative: false, ...schema }, null, 2) : "Result shape not found.");
}

// --- Reasoning Guard Simulator ---
function setupReasoningGuard() {
  $("runGuardSimBtn").addEventListener("click", () => {
    const model = $("guardModel").value;
    const level = $("guardLevel").value;

    const weights = { none: 0, low: 1, medium: 2, high: 3 };
    // Current bundled profiles omit reasoning_effort. The runtime normalizes
    // omission to "none"; never infer capability from a model slug.
    const modelEfforts = {
      "grok-build-0.1": "none",
      "grok-4.3": "none",
      "grok-4.5": "none",
    };
    const modelWeights = {
      "grok-build-0.1": 0,
      "grok-4.3": 0,
      "grok-4.5": 0,
    };

    const requiredWeight = weights[level];
    const modelWeight = modelWeights[model] || 0;

    const card = $("guardResultCard");
    card.className = "simulation-card";

    if (modelWeight < requiredWeight) {
      card.classList.add("block");
      $("simStatus").innerText = "BLOCKED BY GUARD";
      renderGuardExplanation({ blocked: true, model, modelEffort: modelEfforts[model], modelWeight, level, requiredWeight });
    } else {
      card.classList.add("pass");
      $("simStatus").innerText = "PASS";
      renderGuardExplanation({ blocked: false, model, modelEffort: modelEfforts[model], modelWeight, level, requiredWeight });
    }
  });
}

function renderGuardExplanation({ blocked, model, modelEffort, modelWeight, level, requiredWeight }) {
  const explanation = $("simExplanation");
  const lead = document.createElement("strong");
  lead.textContent = blocked ? "Error:" : "Success:";
  const modelCode = document.createElement("code");
  modelCode.textContent = model;
  const detail = document.createTextNode(blocked
    ? ` has bundled profile effort '${modelEffort}' (weight ${modelWeight}), which falls below your required threshold of '${level}' (weight ${requiredWeight}).`
    : ` has bundled profile effort '${modelEffort}' (weight ${modelWeight}), which satisfies the required guard threshold of '${level}' (weight ${requiredWeight}).`);
  const outcome = document.createElement("p");
  outcome.textContent = blocked
    ? "The profile gate will abort before inference execution; model discovery may already have occurred."
    : "The profile gate would not block this request. This does not prove route availability or answer quality.";
  explanation.replaceChildren(lead, document.createTextNode(blocked ? " The target model " : " Model "), modelCode, detail, outcome);
}

// --- OKF Browser & Markdown Parser ---
async function loadOkfManifest() {
  const listContainer = $("okfFileList");
  listContainer.innerHTML = '<div class="okf-item loading">Fetching manifest...</div>';

  try {
    const res = await fetch("/docs/okf/okf-manifest.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.okfManifest = data;

    listContainer.innerHTML = "";
    data.files.forEach((file) => {
      // Get filename without path
      const baseName = file.split("/").pop();
      // A real button so the list is keyboard-operable, not click-only.
      const item = document.createElement("button");
      item.type = "button";
      const active = baseName === state.activeOkfFile;
      item.className = "okf-item" + (active ? " active" : "");
      item.setAttribute("aria-current", active ? "true" : "false");
      item.innerText = baseName;
      item.addEventListener("click", () => {
        document.querySelectorAll(".okf-item").forEach((i) => {
          i.classList.remove("active");
          i.setAttribute("aria-current", "false");
        });
        item.classList.add("active");
        item.setAttribute("aria-current", "true");
        state.activeOkfFile = baseName;
        loadOkfFile(baseName);
      });
      listContainer.appendChild(item);
    });

    loadOkfFile(state.activeOkfFile);
  } catch (err) {
    const failure = document.createElement("div");
    failure.className = "okf-item";
    failure.style.borderColor = "var(--red)";
    failure.style.color = "var(--red)";
    failure.textContent = `Failed: ${err.message}`;
    listContainer.replaceChildren(failure);
  }
}

async function loadOkfFile(fileName) {
  $("okfFileName").innerText = fileName;
  const viewer = $("okfContentArea");
  viewer.innerText = "Loading file content...";

  try {
    const res = await fetch(`/docs/okf/${fileName}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();

    // Large file check (> 50KB)
    if (text.length > 50000) {
      const warningDiv = document.createElement("div");
      warningDiv.style.background = "rgba(240, 58, 71, 0.1)";
      warningDiv.style.color = "var(--red)";
      warningDiv.style.padding = "10px";
      warningDiv.style.marginBottom = "10px";
      warningDiv.style.borderRadius = "var(--radius)";
      warningDiv.innerText = `⚠️ Warning: Large file loaded (${Math.round(text.length / 1024)} KB). Rerouted to lazy plain-text parser for safety.`;
      viewer.replaceChildren(warningDiv);

      const pre = document.createElement("pre");
      pre.innerText = text;
      viewer.appendChild(pre);
      return;
    }

    // Strip YAML frontmatter
    let cleanText = text;
    if (text.startsWith("---")) {
      const parts = text.split("---");
      if (parts.length >= 3) {
        cleanText = parts.slice(2).join("---").trim();
      }
    }

    renderMarkdownInto(viewer, cleanText);
  } catch (err) {
    const failure = document.createElement("p");
    failure.style.color = "var(--red)";
    failure.textContent = `Failed to render file: ${err.message}`;
    viewer.replaceChildren(failure);
  }
}

// Shared injection point for the escape-first renderer in markdown.js.
function renderMarkdownInto(element, markdown) {
  // parseMarkdown escapes all source HTML before adding its fixed tags.
  // lgtm[js/xss-through-dom]
  const parsed = new DOMParser().parseFromString(parseMarkdown(markdown), "text/html");
  element.replaceChildren(...Array.from(parsed.body.childNodes));
}

function setupOkfClipboard() {
  $("copyOkfPathBtn").addEventListener("click", function() {
    const path = `/docs/okf/${state.activeOkfFile}`;
    copyTextToClipboard(path, this);
  });
}

// --- WebMCP manifest & Bridge ---
async function loadWebMcpManifest() {
  try {
    const res = await fetch("/.well-known/webmcp");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    $("webmcpManifestCode").innerText = JSON.stringify(data, null, 2);
  } catch (err) {
    $("webmcpManifestCode").innerText = `Failed to fetch manifest: ${err.message}`;
  }
}

function checkWebMcpBridge() {
  const ctx = window.document?.modelContext || window.navigator?.modelContext;
  const card = $("webmcpBridgeStatus");

  card.className = "bridge-status-card";
  if (ctx && typeof ctx.registerTool === "function") {
    card.classList.add("connected");
    card.querySelector(".bridge-title").innerText = "WebMCP Bridge Detected!";
    card.querySelector(".bridge-desc").innerText = "window.document.modelContext is active and our tools have been registered in the browser agent scope.";
  } else {
    card.classList.add("unconnected");
    card.querySelector(".bridge-title").innerText = "No WebMCP Bridge Found";
    card.querySelector(".bridge-desc").innerText = "document.modelContext is not available. Use a compatible experimental browser build or bridge extension.";
  }
}

// --- Telemetry & Metrics Dashboard ---
async function fetchLiveMetrics() {
  setText("rawMetricsReport", "Polling structured MCP metrics...");
  setText("metricsStatus", "Refreshing local usage ledger…");
  try {
    const res = await fetchMcpCall("grok_mcp_status", { view: "json" });
    const payload = extractToolPayload(res);
    if (!payload?.usage?.today || !payload?.usage?.lifetime) {
      throw new Error("MCP returned an unsupported metrics payload");
    }
    state.metricsSnapshot = payload;
    renderMetricsSnapshot();
    renderSpendGlance(payload);
  } catch (err) {
    setText("rawMetricsReport", `Failed to fetch telemetry report: ${err.message}`);
    setText("metricsStatus", "Metrics unavailable — the MCP status call failed.");
  }
}

function formatMetric(value, formatter, empty = "—") {
  return value === null || value === undefined ? empty : formatter(Number(value));
}

function formatTokens(value) {
  const number = Number(value || 0);
  if (number >= 1000000) return `${(number / 1000000).toFixed(2)}M`;
  if (number >= 1000) return `${(number / 1000).toFixed(1)}K`;
  return number.toLocaleString();
}

function planeRequests(planes, names) {
  return names.reduce((total, name) => total + Number(planes?.[name]?.requests || 0), 0);
}

function renderBreakdownList(containerId, entries, valueLabel) {
  const container = $(containerId);
  container.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("span");
    empty.className = "empty-cell";
    empty.textContent = "No attributed activity yet.";
    container.appendChild(empty);
    return;
  }
  for (const [label, value] of entries.slice(0, 8)) {
    const row = document.createElement("div");
    row.className = "breakdown-row";
    const name = document.createElement("span");
    name.textContent = label;
    const amount = document.createElement("strong");
    amount.textContent = valueLabel(value);
    row.append(name, amount);
    container.appendChild(row);
  }
}

function routingLabel(value) {
  return String(value || "unknown").replaceAll("_", " ");
}

function renderRoutingReceipts(receipts) {
  const container = $("routingReceipts");
  container.replaceChildren();
  if (!receipts?.length) {
    const empty = document.createElement("span");
    empty.className = "empty-cell";
    empty.textContent = "No v1 routing receipts in this period. Older telemetry remains valid.";
    container.appendChild(empty);
    return;
  }
  for (const item of receipts) {
    const receipt = item.routing || {};
    const details = document.createElement("details");
    details.className = "routing-receipt";
    const summary = document.createElement("summary");

    const primary = document.createElement("span");
    primary.className = "receipt-primary";
    const model = document.createElement("strong");
    model.textContent = receipt.resolved_model || "unknown model";
    const route = document.createElement("span");
    route.className = "state-chip local";
    route.textContent = routingLabel(receipt.route_class);
    primary.append(model, route);

    const reason = document.createElement("span");
    reason.className = "receipt-reason";
    reason.textContent = routingLabel(receipt.why_detail || receipt.why);

    const operational = document.createElement("span");
    operational.className = "receipt-operational";
    const time = item.created_at ? new Date(item.created_at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "—";
    operational.textContent = `${item.caller || "unattributed"} • ${item.plane || "unknown"} • ${time}`;
    summary.append(primary, reason, operational);

    const body = document.createElement("div");
    body.className = "receipt-body";
    const facts = document.createElement("div");
    facts.className = "receipt-facts";
    const factValues = [
      ["Mode", receipt.mode],
      ["Evidence", receipt.evidence_source],
      ["Catalog", receipt.catalog?.source],
      ["Tokens", formatTokens(item.tokens)],
      ["Latency", `${Number(item.latency_sec || 0).toFixed(2)}s`],
      ["Result", item.success ? "success" : "failed"],
    ];
    for (const [label, value] of factValues) {
      const fact = document.createElement("span");
      const key = document.createElement("b");
      key.textContent = `${label}: `;
      fact.append(key, document.createTextNode(routingLabel(value)));
      facts.appendChild(fact);
    }
    const raw = document.createElement("pre");
    raw.className = "receipt-json";
    raw.textContent = JSON.stringify({features: receipt.features, candidates: receipt.candidates, fallback: receipt.fallback || null}, null, 2);
    body.append(facts, raw);
    details.append(summary, body);
    container.appendChild(details);
  }
}

function formatVerifiedSuccess(metrics) {
  const verified = Number(metrics?.verified_outcomes || 0);
  if (!verified) return "—";
  if (metrics.success_rate === null || metrics.success_rate === undefined) return "—";
  return `${(metrics.success_rate * 100).toFixed(1)}% of ${verified}`;
}

function formatVerifiedSplit(metrics) {
  const verified = Number(metrics?.verified_outcomes || 0);
  const unverified = Number(metrics?.unverified_requests || 0);
  if (!verified && !unverified && !Number(metrics?.requests || 0)) return "—";
  return `${verified} / ${unverified}`;
}

function renderPlaneTable(planes) {
  const body = $("planeBreakdownBody");
  body.replaceChildren();
  const entries = Object.entries(planes || {});
  if (!entries.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "empty-cell";
    cell.textContent = "No model executions in this period.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  for (const [plane, metrics] of entries) {
    const row = document.createElement("tr");
    const success = formatVerifiedSuccess(metrics);
    const latency = metrics.avg_latency_sec === null ? "—" : `${metrics.avg_latency_sec.toFixed(2)}s`;
    const cost = plane === "API" ? `$${Number(metrics.api_cost_usd || 0).toFixed(5)}` : "Subscription";
    const values = [plane, metrics.requests, success, latency, formatTokens(metrics.tracked_tokens), cost];
    values.forEach((value, index) => {
      const cell = document.createElement(index === 0 ? "th" : "td");
      cell.textContent = String(value);
      row.appendChild(cell);
    });
    body.appendChild(row);
  }
}

function renderMetricsSnapshot() {
  const payload = state.metricsSnapshot;
  if (!payload) return;
  const period = payload.usage[state.metricsPeriod];
  const summary = period.summary;
  const planes = period.planes || {};
  const apiRequests = planeRequests(planes, ["API"]);
  const cliRequests = planeRequests(planes, ["CLI", "CLI-Fallback"]);
  const verified = Number(summary.verified_outcomes || 0);
  const unverified = Number(summary.unverified_requests || 0);

  $("metricApiCost").innerText = `$${Number(summary.api_cost_usd || 0).toFixed(5)}`;
  $("metricRequests").innerText = Number(summary.requests || 0).toLocaleString();
  $("metricLatency").innerText = formatMetric(summary.avg_latency_sec, (v) => `${v.toFixed(2)}s`);
  $("metricSuccess").innerText = formatVerifiedSuccess(summary);
  if ($("metricSuccessSub")) {
    $("metricSuccessSub").innerText = verified
      ? `Of ${verified} receipt-verified outcome${verified === 1 ? "" : "s"} only`
      : "No receipt-verified outcomes yet (most stops stay unverified)";
  }
  if ($("metricVerifiedSplit")) {
    $("metricVerifiedSplit").innerText = formatVerifiedSplit(summary);
  }
  $("metricPlane").innerText = `${apiRequests} / ${cliRequests}`;
  $("metricTokens").innerText = formatTokens(summary.tracked_tokens);

  const quality = payload.usage.data_quality;
  const periodLabel = state.metricsPeriod === "today" ? "today" : "across the local ledger";
  $("metricsCoverage").innerText = summary.requests
    ? `${summary.requests} request${summary.requests === 1 ? "" : "s"} ${periodLabel} (${verified} verified, ${unverified} unverified). ${summary.routing_receipt_requests || 0} row${summary.routing_receipt_requests === 1 ? "" : "s"} include explainable routing receipts. Success % is never over all requests — only over verified outcomes.`
    : `No model executions ${periodLabel}. Health checks, discovery, and status calls intentionally do not create billable telemetry.`;

  const breakerCount = Object.values(payload.circuit_breakers || {}).filter((item) => item?.open).length;
  $("metricsStatus").innerText = `Live local ledger • ${quality.telemetry_rows} stored row${quality.telemetry_rows === 1 ? "" : "s"} • ${breakerCount ? `${breakerCount} breaker open` : "all breakers closed"}`;

  renderPlaneTable(planes);

  const provider = payload.usage.api_billing.provider || {};
  $("providerUsageState").innerText = String(provider.state || "unknown").replaceAll("_", " ");
  $("providerUsageState").className = `state-chip ${provider.state === "ready" ? "ready" : ""}`;
  $("providerUsageValue").innerText = provider.usage_usd === null || provider.usage_usd === undefined
    ? "Not connected"
    : `$${Number(provider.usage_usd).toFixed(5)} team-wide today`;
  $("providerUsageDetail").innerText = provider.detail || "Local API cost tracking works without optional organization billing setup.";

  $("cliUsageValue").innerText = `${cliRequests} locally tracked request${cliRequests === 1 ? "" : "s"}`;
  $("cliUsageDetail").innerText = payload.usage.cli_subscription.detail;

  renderBreakdownList(
    "modelBreakdown",
    Object.entries(summary.models || {}),
    (value) => `${value} request${value === 1 ? "" : "s"}`
  );
  $("callerBreakdownTitle").innerText = `Callers • ${Number(summary.caller_attributed_requests || 0)}/${Number(summary.requests || 0)} attributed`;
  renderBreakdownList(
    "callerBreakdown",
    Object.entries(period.callers || payload.callers || {}),
    (value) => `${value.requests} req • $${Number(value.total_cost_usd || 0).toFixed(4)}`
  );
  renderBreakdownList(
    "routeClassBreakdown",
    Object.entries(summary.route_classes || {}),
    (value) => `${value} request${value === 1 ? "" : "s"}`
  );
  renderBreakdownList(
    "selectionReasonBreakdown",
    Object.entries(summary.selection_reasons || {}),
    (value) => `${value} request${value === 1 ? "" : "s"}`
  );
  renderRoutingReceipts(period.recent_routes || []);

  $("rawMetricsReport").innerText = JSON.stringify(payload, null, 2);
}

function setupMetricsControls() {
  document.querySelectorAll(".period-btn").forEach((button) => {
    button.addEventListener("click", () => {
      state.metricsPeriod = button.dataset.period || "today";
      document.querySelectorAll(".period-btn").forEach((item) => item.classList.toggle("active", item === button));
      renderMetricsSnapshot();
    });
  });
}

// --- Discover Self Onboarding ---
async function runDiscoverSelfOnboarding() {
  const code = $("discoverSelfCode");
  const details = code?.closest("details");
  if (details) details.open = true;
  if (code) code.textContent = "Calling discover_self...";
  try {
    const res = await fetchMcpCall("grok_mcp_discover_self", {});
    const payload = extractToolPayload(res);
    if (code) code.textContent = JSON.stringify(payload, null, 2);
  } catch (err) {
    if (code) code.textContent = `Error: ${err.message}`;
  }
}

// --- Quick Test Console / JSON-RPC Core ---

// Authoritative readiness, owned by /readyz probes (runStartupCheck, pollReadyz).
// fetchRuntimeStatus reads /runtimez, which returns 200 even when the gateway is
// not ready, so it must defer to this rather than unconditionally claiming "live".
const gatewayReadiness = { ready: true, detail: null };

// A 503 from /readyz means the gateway is reachable but a named check failed;
// only a fetch failure means no connection. The banner must not conflate them.
async function describeNotReady(res) {
  try {
    const body = await res.json();
    const failing = Object.entries(body.checks || {})
      .filter(([, ok]) => !ok)
      .map(([name]) => name);
    if (failing.length) {
      return `Gateway reachable but not ready — failing checks: ${failing.join(", ")}.`;
    }
    return `Gateway reachable but not ready (status: ${body.status || res.status}).`;
  } catch {
    return `Gateway reachable but not ready (HTTP ${res.status}).`;
  }
}

async function runStartupCheck() {
  let notReadyDetail = null;
  try {
    const res = await fetch("/readyz");
    if (res.ok) {
      gatewayReadiness.ready = true;
      gatewayReadiness.detail = null;
      setStatus("active", "Live");
      return true;
    }
    notReadyDetail = await describeNotReady(res);
  } catch {
    // fall through: connection-level failure
  }
  gatewayReadiness.ready = false;
  gatewayReadiness.detail = notReadyDetail;
  setStatus("error", notReadyDetail ? "Not Ready" : "Offline");
  renderSetupStatus(null, false, notReadyDetail);
  return false;
}

function resolveMcpEndpoint() {
  // Public product endpoint for pasteable IDE config. Loopback UI always
  // advertises Core :4765/mcp even when the browser preview is on another port
  // (e.g. TestClient :8080 or a reverse proxy). Non-loopback keeps the host
  // that served this page so cloud/deploy previews stay accurate.
  try {
    const u = new URL(window.location.href);
    const host = u.hostname || "localhost";
    const loopback = host === "localhost" || host === "127.0.0.1";
    if (loopback) {
      return `${u.protocol}//${host}:4765/mcp`;
    }
    u.pathname = "/mcp";
    u.search = "";
    u.hash = "";
    return u.toString().replace(/\/$/, "");
  } catch {
    return "http://localhost:4765/mcp";
  }
}

function genericMcpJson(endpoint) {
  // Cursor is the first-class host IDE for UniGrok (xAI family). Other IDEs
  // swap X-Client-ID (claude-code, vscode, codex, antigravity) the same way.
  return JSON.stringify({
    mcpServers: {
      unigrok: {
        url: endpoint,
        headers: { "X-Client-ID": "cursor" },
      },
    },
  }, null, 2);
}

function agentSetupPrompt(endpoint) {
  return [
    "Configure UniGrok MCP for this machine:",
    `- Streamable HTTP URL: ${endpoint}`,
    "- Preferred host IDE: Cursor (X-Client-ID: cursor). Also supported: claude-code, vscode, codex, antigravity",
    "- Never put XAI_API_KEY in IDE MCP settings; credentials stay in UniGrok's server .env",
    "- Cursor may offer non-Grok models natively; UniGrok MCP is for shared Grok planes, cost truth, and @grok",
    "- After connecting, call tools/list and grok_mcp_discover_self (read data.bootstrap when present)",
    "- Prefer the UniGrok agent tool when I say @grok or want a second opinion",
    "- When I ask for a multi-step Implementation Plan, get a UniGrok second opinion",
    "  (agent mode thinking or reasoning) and improve the plan before showing it —",
    "  only if I want that habit; do not silently spend metered API credits",
    "- Do not invent a second MCP port, Forge, or land workflow for ordinary use",
  ].join("\n");
}

function renderConnectSnippets() {
  const endpoint = resolveMcpEndpoint();
  if ($("mcpEndpointDisplay")) $("mcpEndpointDisplay").textContent = endpoint;
  if ($("mcpJsonSnippet")) $("mcpJsonSnippet").textContent = genericMcpJson(endpoint);
  if ($("agentPromptSnippet")) $("agentPromptSnippet").textContent = agentSetupPrompt(endpoint);
  return endpoint;
}

/** Keep the hero primary CTA label aligned with readiness state. */
function syncPrimaryCta(ready, detailText = null) {
  const btn = $("copyPrimaryActionBtn");
  if (!btn) return;
  const offline = !ready && !detailText;
  if (offline) {
    btn.textContent = "Copy install commands";
    btn.dataset.cta = "install";
  } else {
    btn.textContent = "Copy IDE MCP config";
    btn.dataset.cta = "mcp";
  }
}

function renderPlaneCards(contract) {
  const cli = contract?.cli || {};
  const api = contract?.api || {};
  const setChip = (id, available, label) => {
    const el = $(id);
    if (!el) return;
    el.textContent = available ? "ready" : (label || "not ready");
    el.className = `state-chip ${available ? "ready" : ""}`;
  };
  setChip("cliPlaneState", Boolean(cli.available), cli.state || "unavailable");
  setChip("apiPlaneState", Boolean(api.available), api.state || "unavailable");
  if ($("cliPlaneDetail")) {
    $("cliPlaneDetail").textContent = cli.available
      ? "Subscription plane ready. Local request tracking only — provider quota is not exposed."
      : (cli.state === "needs_auth" || cli.auth === "needs_auth")
        ? "CLI OAuth not verified. Use device login on the gateway host, then recheck."
        : "CLI plane unavailable on this machine.";
  }
  if ($("apiPlaneDetail")) {
    $("apiPlaneDetail").textContent = api.available
      ? "Metered developer API key present. Exact response cost tracked when the provider returns it."
      : "No usable XAI_API_KEY in the gateway environment.";
  }
}

function renderSpendGlance(payload) {
  if (!payload?.usage) return;
  const period = payload.usage.today ?? payload.usage.lifetime ?? {};
  const summary = period.summary ?? {};
  const planes = period.planes ?? {};
  const api = planes.API ?? {};
  const cli = planes.CLI ?? {};
  const cliFb = planes["CLI-Fallback"] ?? {};
  const cliReqs = Number(cli.requests ?? 0) + Number(cliFb.requests ?? 0);
  if ($("spendApiToday")) {
    // Prefer nullish coalescing so a legitimate $0 cost is not skipped for a non-zero fallback.
    const usd = summary.api_cost_usd ?? api.api_cost_usd ?? api.total_cost_usd ?? 0;
    $("spendApiToday").textContent = `$${Number(usd).toFixed(4)}`;
  }
  if ($("spendCliRequests")) $("spendCliRequests").textContent = String(cliReqs);
  const verified = Number(summary.verified_outcomes ?? 0);
  const unverified = Number(summary.unverified_requests ?? 0);
  if ($("spendVerifiedSplit")) $("spendVerifiedSplit").textContent = `${verified} / ${unverified}`;
  if ($("spendHonesty")) {
    $("spendHonesty").textContent = verified
      ? `Verified success is of ${verified} receipt-verified outcome${verified === 1 ? "" : "s"} only — not of all requests.`
      : "No receipt-verified outcomes yet. Most provider stops stay unverified until a receipt.";
  }
}

function actionableCredentialNotice(contract) {
  const notices = (contract?.notices || []).filter((notice) => notice.prompt_user);
  return notices.find((notice) => notice.blocking)
    || (contract?.effective_plane === "none" ? notices[0] : null)
    || null;
}

function renderSetupStatus(data, ready = true, detailText = null) {
  // Offline → show install card. Ready-but-degraded → named checks + credential alert.
  $("quickStartCard")?.classList.toggle("hidden", Boolean(ready) || Boolean(detailText));
  const target = $("setupStatusSummary");
  const contract = data?.credential_planes || {};
  const effective = contract.effective_plane || "none";
  const runtime = data?.runtime || "unknown";
  const tools = $("toolCountChip")?.textContent?.replace(/^tools:\s*/i, "") || "…";
  const notice = actionableCredentialNotice(contract);
  const attention = notice?.message || "";

  const hero = $("readinessHero");
  const title = $("readinessTitle");
  const detail = $("readinessDetail");
  if (hero && title && detail) {
    hero.classList.remove("state-checking", "state-ready", "state-attention", "state-offline");
    if (ready) {
      hero.classList.add("state-ready");
      title.textContent = "Gateway is live";
      detail.textContent = `Runtime ${runtime} · effective plane ${effective} · ${tools}.${attention ? ` ${attention}` : ""}`;
    } else if (detailText) {
      hero.classList.add("state-attention");
      title.textContent = "Gateway needs attention";
      detail.textContent = detailText;
    } else {
      hero.classList.add("state-offline");
      title.textContent = "Gateway offline";
      detail.textContent = "Cannot reach /readyz. Start the service, then recheck.";
    }
  }

  if ($("probeAge")) {
    $("probeAge").textContent = `probed ${new Date().toLocaleTimeString()}`;
  }

  syncPrimaryCta(ready, detailText);
  renderPlaneCards(contract);
  renderConnectSnippets();

  if (target) {
    const t = document.createElement("strong");
    t.textContent = ready
      ? "Gateway is live."
      : (detailText ? "Gateway needs attention." : "Gateway offline.");
    const d = document.createElement("span");
    d.textContent = ready
      ? ` Runtime: ${runtime} · active credential plane: ${effective}.${attention ? ` ${attention}` : ""}`
      : ` ${detailText || "The live readiness check failed. Restart or inspect the runtime before running a task."}`;
    target.replaceChildren(t, d);
  }
}

function renderSurfaceModeBadge(data) {
  const badge = $("surfaceModeBadge");
  if (!badge) return;
  const mode = String(data?.service?.mode || "").toLowerCase();
  const port = window.location.port || "";
  const isForgePort = port === "4766";
  if (mode === "contributor" || isForgePort) {
    badge.textContent = "Surface: Contributor Forge";
    badge.title = "Forge attaches the UniGrok checkout. Public product path remains Core :4765.";
    badge.dataset.surface = "forge";
  } else {
    badge.textContent = "Surface: Stable Core";
    badge.title = "Public product path. Daily chat is IDE MCP; this page is health and connect.";
    badge.dataset.surface = "core";
  }
}

function credentialPlaneCatalogSignature(contract) {
  const plane = (name) => {
    const view = contract?.[name] || {};
    return {
      available: Boolean(view.available),
      state: String(view.state || ""),
      auth: String(view.auth || ""),
      binary: Boolean(view.binary),
    };
  };
  return JSON.stringify({
    policy: String(contract?.policy || ""),
    effectivePlane: String(contract?.effective_plane || ""),
    serviceUsable: Boolean(contract?.service_usable),
    degraded: Boolean(contract?.degraded),
    api: plane("api"),
    cli: plane("cli"),
  });
}

async function fetchRuntimeStatus() {
  try {
    const res = await fetch("/runtimez");
    if (!res.ok) throw new Error();
    const data = await res.json();
    if (data.credential_planes) {
      const priorSignature = credentialPlaneCatalogSignature(state.credentialPlanes);
      const nextSignature = credentialPlaneCatalogSignature(data.credential_planes);
      state.credentialPlanes = data.credential_planes;
      if (priorSignature !== nextSignature) {
        // Catalog availability/source is credential-dependent. Invalidate any
        // cached or in-flight snapshot and remove stale plane/model choices.
        state.modelCatalog = null;
        state.modelCatalogGeneration += 1;
        clearPlaneModelLists("Credentials changed. Refreshing model catalog…");
        clearModelOptions("auto route");
        if (state.activeTab === "tab-models") {
          void loadPlaneModelCatalog(true);
        }
      }
    }
    $("runtimeChip").innerText = `runtime: ${data.runtime || "unknown"}`;
    $("transportChip").innerText = `transport: ${data.transport || "unknown"}`;
    renderSurfaceModeBadge(data);
    renderCredentialPlanes(data.credential_planes || null);
    // /runtimez is 200 even when not ready, so honor the readiness probe.
    renderSetupStatus(data, gatewayReadiness.ready, gatewayReadiness.detail);
    return data;
  } catch {
    $("runtimeChip").innerText = "runtime: unknown";
    $("transportChip").innerText = "transport: unknown";
    $("planeChip").innerText = "plane: unknown";
    renderSurfaceModeBadge(null);
    renderSetupStatus(null, false);
    return null;
  }
}

function renderCredentialPlanes(contract) {
  const alertCard = $("credentialAlert");
  const planeChip = $("planeChip");
  if (!contract || !alertCard || !planeChip) return;

  const preferred = contract.preferred_plane || "unknown";
  const effective = contract.effective_plane || "none";
  planeChip.innerText = `plane: ${preferred} first → ${effective}`;

  const notice = actionableCredentialNotice(contract);
  if (!notice) {
    alertCard.classList.add("hidden");
    return;
  }

  const plane = notice.plane === "CLI" ? contract.cli : contract.api;
  const action = (plane && plane.action) || {};
  $("credentialAlertTitle").innerText = notice.blocking
    ? "Model access needs setup"
    : `${notice.plane} plane needs attention`;
  $("credentialAlertMessage").innerText = ` ${notice.message}`;
  alertCard.classList.toggle("blocking", Boolean(notice.blocking));
  alertCard.classList.remove("hidden");

  const command = $("credentialActionCommand");
  const copyButton = $("copyCredentialActionBtn");
  const safeCommand = typeof action.command === "string" ? action.command : "";
  command.innerText = safeCommand;
  command.classList.toggle("hidden", !safeCommand);
  copyButton.classList.toggle("hidden", !safeCommand);
  copyButton.dataset.command = safeCommand;
}

async function fetchMcpListTools() {
  const reqId = state.requestIdCounter++;
  const requestPayload = {
    jsonrpc: "2.0",
    method: "tools/list",
    params: {},
    id: reqId
  };
  try {
    const headers = {
      "Content-Type": "application/json",
      "Accept": "application/json, text/event-stream",
      "X-Client-ID": $("clientIdInput")?.value || "mcp-ui-client",
    };
    if (state.clientToken) {
      headers["Authorization"] = `Bearer ${state.clientToken}`;
    }
    const res = await fetch("/mcp", {
      method: "POST",
      headers: headers,
      body: JSON.stringify(requestPayload)
    });
    if (!res.ok) throw new Error();
    const data = await parseMcpResponse(res);
    if (data.result && data.result.tools) {
      const count = data.result.tools.length;
      const chip = $("toolCountChip");
      if (chip) chip.innerText = `tools: ${count}`;
    }
  } catch (err) {
    console.error("Failed to list tools: ", err);
    const chip = $("toolCountChip");
    if (chip) chip.innerText = "tools: error";
  }
}

async function loadModelsList() {
  try {
    const res = await fetch("/v1/models");
    if (!res.ok) throw new Error();
    const data = await res.json();
    state.models = data.data || [];
    // /v1/models is API-compatible and intentionally does not own the plane-
    // aware selector. grok_mcp_discover_self is the only source for that UI.
  } catch (err) {
    console.error("Failed to load models list: ", err);
  }
}

function syncModelOptions() {
  const select = $("modelInput");
  const plane = $("planeInput")?.value || "auto";
  const catalog = state.modelCatalog;
  if (!select || !catalog?.planes) return;
  const previous = select.value;
  const cliModels = catalog.planes.CLI?.models || [];
  const apiModels = catalog.planes.API?.models || [];
  const apiIds = new Set(apiModels.map((model) => String(model?.id || "")));
  const cliOnlyModels = cliModels.filter((model) => !apiIds.has(String(model?.id || "")));
  const groups = plane === "cli"
    ? [["CLI subscription models", cliModels]]
    : plane === "api"
      ? [["Metered API models", apiModels]]
      : [["Direct CLI-only pins", cliOnlyModels], ["Explicit pins use metered API", apiModels]];
  select.replaceChildren();
  const automatic = document.createElement("option");
  automatic.value = "";
  automatic.innerText = plane === "cli" ? "auto CLI model (Recommended)" : plane === "api" ? "auto API model" : "auto route";
  select.appendChild(automatic);
  const seen = new Set();
  for (const [label, models] of groups) {
    const group = document.createElement("optgroup");
    group.label = label;
    for (const model of models) {
      const id = String(model?.id || "");
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const option = document.createElement("option");
      option.value = id;
      option.innerText = id;
      group.appendChild(option);
    }
    if (group.children.length) select.appendChild(group);
  }
  select.value = seen.has(previous) ? previous : "";
}

function clearModelOptions(label = "auto route") {
  const select = $("modelInput");
  if (!select) return;
  select.replaceChildren();
  const automatic = document.createElement("option");
  automatic.value = "";
  automatic.innerText = label;
  select.appendChild(automatic);
}

function updatePlaneControls() {
  const plane = $("planeInput")?.value || "auto";
  const hint = $("planeHint");
  const fallback = $("fallbackPolicyInput");
  if (!hint || !fallback) return;
  if (plane === "cli") {
    hint.innerText = "Subscription only · no developer API billing";
    hint.classList.remove("metered");
    fallback.value = "same_plane";
  } else if (plane === "api") {
    hint.innerText = "Metered developer API · exact cost reported after execution";
    hint.classList.add("metered");
    fallback.value = "same_plane";
  } else {
    hint.innerText = "Subscription first · cross-plane fallback may incur API charges";
    hint.classList.add("metered");
  }
  // Console plane pins filter from the dual-plane catalog. Startup only warms
  // /v1/models (API). Load plane catalogs when the user changes plane before
  // visiting Planes, so CLI pins are not API-only slugs with same_plane.
  if (!state.modelCatalog && !state.modelCatalogLoading) {
    loadPlaneModelCatalog(false);
  } else {
    syncModelOptions();
  }
}

function readableCatalogSource(source) {
  const labels = {
    grok_cli: "Live Grok CLI",
    "cli-fallback": "Fallback list (not live)",
    "cloudrun-disabled": "Unavailable in Cloud Run",
    xai_api: "Live xAI API",
    xai_api_fallback: "Fallback list (not live)",
    skipped: "Not queried",
  };
  return labels[source] || String(source || "Unknown").replaceAll("_", " ");
}

function isFallbackCatalogSource(source) {
  const key = String(source || "");
  return FALLBACK_CATALOG_SOURCES.has(key) || key.includes("fallback");
}

function planePinSnippet(planeName, modelId) {
  const plane = planeName === "CLI" ? "cli" : "api";
  return [
    `model=${modelId}`,
    `plane=${plane}`,
    "fallback_policy=same_plane",
    `# UniGrok agent pin — ${planeName} credential plane only`,
  ].join("\n");
}

function clearPlaneModelLists(message) {
  for (const prefix of ["cli", "api"]) {
    const list = $(`${prefix}ModelList`);
    if (!list) continue;
    list.replaceChildren();
    const empty = document.createElement("span");
    empty.className = "empty-cell";
    empty.innerText = message;
    list.appendChild(empty);
  }
}

function renderPlaneRepair(prefix, planeKey, contract) {
  const host = $(`${prefix}PlaneRepair`);
  if (!host) return;
  host.replaceChildren();
  host.classList.add("hidden");
  if (!contract) return;

  const plane = planeKey === "CLI" ? contract.cli : contract.api;
  const notices = (contract.notices || []).filter(
    (notice) => notice && String(notice.plane || "").toUpperCase() === planeKey,
  );
  const notice = notices.find((item) => item.prompt_user) || notices[0];
  const action = (plane && plane.action) || (notice && notice.action) || null;
  const needsRepair = plane && plane.available === false;
  if (!needsRepair && !notice) return;

  host.classList.remove("hidden");
  const title = document.createElement("strong");
  title.innerText = notice?.blocking
    ? `${planeKey} plane blocked`
    : `${planeKey} plane needs attention`;
  const message = document.createElement("p");
  message.innerText = notice?.message
    || (planeKey === "CLI"
      ? "CLI subscription plane is not ready. Device-login on the gateway host, then refresh."
      : "API plane is not ready. Configure XAI_API_KEY in the UniGrok server .env (never in IDE MCP JSON).");
  host.append(title, message);

  const command = typeof action?.command === "string" ? action.command : "";
  if (command) {
    const pre = document.createElement("pre");
    pre.className = "plane-repair-command";
    pre.textContent = command;
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "small-btn-glow";
    copyBtn.innerText = "Copy repair command";
    copyBtn.addEventListener("click", () => copyTextToClipboard(command, copyBtn));
    host.append(pre, copyBtn);
  }
}

function renderCatalogTrustBanner(prefix, plane) {
  const banner = $(`${prefix}CatalogTrust`);
  if (!banner) return;
  const source = plane?.source;
  if (isFallbackCatalogSource(source) || (plane?.credential_available && !plane?.catalog_available)) {
    banner.classList.remove("hidden");
    banner.innerText = "Not live — static fallback catalog. Do not treat pins as verified on this plane until source is Live.";
    return;
  }
  banner.classList.add("hidden");
  banner.innerText = "";
}

function renderPlaneModels(planeName, plane, sharedIds, contract) {
  const prefix = planeName === "CLI" ? "cli" : "api";
  const stateChip = $(`${prefix}ModelPlaneState`);
  const source = $(`${prefix}ModelSource`);
  const economics = $(`${prefix}ModelEconomics`);
  const list = $(`${prefix}ModelList`);
  const models = Array.isArray(plane?.models) ? plane.models : [];
  const fallback = isFallbackCatalogSource(plane?.source)
    || (plane?.credential_available && !plane?.catalog_available);

  const planeState = plane?.available
    ? "Ready"
    : fallback
      ? "Catalog fallback"
      : String(plane?.credential_state || "Unavailable").replaceAll("_", " ");
  if (stateChip) {
    stateChip.innerText = planeState;
    stateChip.className = `state-chip ${plane?.available ? "ready" : ""} ${fallback ? "fallback" : ""}`;
  }
  if (source) source.innerText = readableCatalogSource(plane?.source);
  if (economics) economics.innerText = plane?.economics || "Usage terms unavailable.";
  if (prefix === "cli" && $("cliDefaultModel")) {
    $("cliDefaultModel").innerText = plane?.default_model || "Not reported";
  }
  if (prefix === "api" && $("apiDefaultModel")) {
    $("apiDefaultModel").innerText = plane?.default_model
      || "No single default — agent auto-routes (planning/coding aliases)";
  }

  renderCatalogTrustBanner(prefix, plane);
  renderPlaneRepair(prefix, planeName, contract);

  if (!list) return;
  list.replaceChildren();
  if (!models.length) {
    const empty = document.createElement("span");
    empty.className = "empty-cell";
    empty.innerText = `No ${planeName} models reported.`;
    list.appendChild(empty);
    return;
  }

  for (const model of models) {
    const id = String(model?.id || "unknown");
    const card = document.createElement("article");
    card.className = `provider-model-card${fallback ? " fallback-catalog" : ""}`;

    const identity = document.createElement("div");
    const name = document.createElement("strong");
    name.innerText = id;
    identity.appendChild(name);

    const badges = document.createElement("div");
    badges.className = "model-badges";
    const planeBadge = document.createElement("span");
    planeBadge.className = `model-badge ${prefix}`;
    planeBadge.innerText = planeName === "CLI" ? "CLI subscription" : "API metered";
    badges.appendChild(planeBadge);

    if (fallback) {
      const fallbackBadge = document.createElement("span");
      fallbackBadge.className = "model-badge fallback";
      fallbackBadge.innerText = "Fallback";
      badges.appendChild(fallbackBadge);
    }

    if (model?.default || id === plane?.default_model) {
      const defaultBadge = document.createElement("span");
      defaultBadge.className = "model-badge default";
      defaultBadge.innerText = "Default";
      badges.appendChild(defaultBadge);
    }
    if (sharedIds.has(id)) {
      const sharedBadge = document.createElement("span");
      sharedBadge.className = "model-badge shared";
      sharedBadge.innerText = "Also on other plane";
      badges.appendChild(sharedBadge);
    }
    if (model?.context_window) {
      const contextBadge = document.createElement("span");
      contextBadge.className = "model-badge context";
      contextBadge.innerText = `${Number(model.context_window).toLocaleString()} context`;
      badges.appendChild(contextBadge);
    }
    identity.appendChild(badges);

    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.className = "small-btn model-copy-btn";
    copyButton.innerText = "Copy pin";
    copyButton.setAttribute("aria-label", `Copy ${planeName} plane pin for ${id}`);
    copyButton.title = "Copies plane-qualified pin (model + plane + same_plane)";
    const pin = planePinSnippet(planeName, id);
    copyButton.addEventListener("click", () => copyTextToClipboard(pin, copyButton));

    card.append(identity, copyButton);
    list.appendChild(card);
  }
}

function renderPlaneModelCatalog(catalog, contract = null) {
  const routing = catalog?.routing || {};
  const planes = catalog?.planes || {};
  const sharedIds = new Set(catalog?.shared_model_ids || []);
  const planesContract = contract || state.credentialPlanes;
  state.modelCatalog = catalog;
  if (contract) state.credentialPlanes = contract;
  syncModelOptions();

  if ($("modelsRoutingPolicy")) {
    $("modelsRoutingPolicy").innerText = String(routing.policy || "unknown").replaceAll("_", " ");
  }
  if ($("modelsPreferredPlane")) {
    $("modelsPreferredPlane").innerText = `Preferred: ${routing.preferred_plane || "none"}`;
  }
  if ($("modelsEffectivePlane")) {
    $("modelsEffectivePlane").innerText = `Effective now: ${routing.effective_plane || "none"}`;
  }
  if ($("modelsRoutingRule")) {
    $("modelsRoutingRule").innerText = routing.rule
      || "Model and credential-plane selection are separate routing decisions. Host IDEs (Cursor, etc.) may list non-Grok models natively — those are outside UniGrok.";
  }

  renderPlaneModels("CLI", planes.CLI || {}, sharedIds, planesContract);
  renderPlaneModels("API", planes.API || {}, sharedIds, planesContract);

  const total = (planes.CLI?.models?.length || 0) + (planes.API?.models?.length || 0);
  const generated = catalog?.generated_at ? new Date(catalog.generated_at).toLocaleString() : "just now";
  if ($("modelsStatus")) {
    $("modelsStatus").innerText = `${total} plane-specific model entr${total === 1 ? "y" : "ies"} • refreshed ${generated}`;
  }

  const sharedNote = $("sharedModelsNote");
  if (sharedNote) {
    if (sharedIds.size) {
      sharedNote.classList.remove("hidden");
      sharedNote.innerText = `${[...sharedIds].join(", ")} ${sharedIds.size === 1 ? "exists" : "exist"} on both UniGrok planes. Shown twice intentionally — authentication, availability, and usage accounting differ. Copy pin is plane-qualified.`;
    } else {
      sharedNote.classList.add("hidden");
      sharedNote.innerText = "";
    }
  }

  const warningList = $("modelCatalogWarnings");
  if (warningList) {
    warningList.replaceChildren();
    const warnings = Array.isArray(catalog?.warnings) ? catalog.warnings : [];
    warningList.classList.toggle("hidden", warnings.length === 0);
    for (const warning of warnings) {
      const item = document.createElement("p");
      item.innerText = warning;
      warningList.appendChild(item);
    }
  }
}

async function loadPlaneModelCatalog(force = true) {
  // Cache successful catalogs until the user forces refresh or opens Planes
  // after a prior failure.
  if (!force && state.modelCatalog && !state.modelCatalogLoading) {
    renderPlaneModelCatalog(state.modelCatalog, state.credentialPlanes);
    return;
  }
  if (state.modelCatalogLoading) return;

  const generation = state.modelCatalogGeneration;

  const refreshButton = $("refreshModelsBtn");
  if (refreshButton) refreshButton.disabled = true;
  state.modelCatalogLoading = true;
  if ($("modelsStatus")) {
    $("modelsStatus").innerText = "Refreshing live CLI and API catalogs through MCP discovery…";
  }
  try {
    const res = await fetchMcpCall("grok_mcp_discover_self", { include_models: true });
    const payload = extractToolPayload(res);
    const catalog = payload?.data?.model_catalog;
    const contract = payload?.data?.credential_planes || state.credentialPlanes;
    if (!catalog) throw new Error("Discovery response did not include a model catalog.");
    if (generation !== state.modelCatalogGeneration) return;
    renderPlaneModelCatalog(catalog, contract);
  } catch (err) {
    if (generation !== state.modelCatalogGeneration) return;
    state.modelCatalog = null;
    clearModelOptions("auto route");
    if ($("modelsStatus")) {
      $("modelsStatus").innerText = `Model catalog unavailable: ${err.message}`;
    }
    clearPlaneModelLists(`Failed to load catalog: ${err.message}`);
    for (const prefix of ["cli", "api"]) {
      const chip = $(`${prefix}ModelPlaneState`);
      if (chip) {
        chip.innerText = "Error";
        chip.className = "state-chip";
      }
      const banner = $(`${prefix}CatalogTrust`);
      if (banner) {
        banner.classList.remove("hidden");
        banner.innerText = "Catalog request failed. Fix MCP connectivity or credentials, then Refresh.";
      }
    }
  } finally {
    state.modelCatalogLoading = false;
    if (refreshButton) refreshButton.disabled = false;
    if (generation !== state.modelCatalogGeneration && !state.modelCatalog) {
      // A credential recheck invalidated this in-flight response. Start one
      // fresh request after releasing the single-flight guard.
      void loadPlaneModelCatalog(false);
    }
  }
}

function setStatus(kind, label) {
  const pill = $("connectionState");
  if (!pill) return;
  pill.className = `status-pill status-${kind}`;
  pill.innerText = label;
}

// JSON-RPC Wire Log Helper
function logRpcTransaction(request, response, elapsed, isError = false) {
  const logsContainer = $("rpcLogs");
  if (!logsContainer) return;
  const placeholder = logsContainer.querySelector(".log-placeholder");
  if (placeholder) placeholder.remove();

  const item = document.createElement("div");
  item.className = `rpc-log-item${isError ? " error-log" : ""}`;

  const elapsedLabel = elapsed ? `${elapsed}ms` : "-";

  const meta1 = document.createElement("div");
  meta1.className = "log-meta";

  const methodName = document.createElement("span");
  methodName.className = "method-name";
  methodName.textContent = request.method || "";
  meta1.appendChild(methodName);

  const reqInfo = document.createElement("span");
  reqInfo.textContent = `id: ${request.id} (${elapsedLabel})`;
  meta1.appendChild(reqInfo);

  const body1 = document.createElement("div");
  body1.className = "log-body";
  body1.textContent = JSON.stringify(request.params, null, 2);

  const meta2 = document.createElement("div");
  meta2.className = "log-meta";
  meta2.style.marginTop = "6px";

  const respLabel = document.createElement("span");
  respLabel.textContent = "Response";
  meta2.appendChild(respLabel);

  const statusTag = document.createElement("span");
  if (isError) {
    statusTag.className = "error-tag";
    statusTag.textContent = "Error";
  } else {
    statusTag.textContent = "OK";
  }
  meta2.appendChild(statusTag);

  const body2 = document.createElement("div");
  body2.className = "log-body";
  body2.textContent = JSON.stringify(response, null, 2);

  item.appendChild(meta1);
  item.appendChild(body1);
  item.appendChild(meta2);
  item.appendChild(body2);

  logsContainer.appendChild(item);
  logsContainer.scrollTop = logsContainer.scrollHeight;
}

async function parseMcpResponse(res) {
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("text/event-stream")) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let done = false;
    let responsePayload;

    while (!done) {
      const { value, done: streamDone } = await reader.read();
      done = streamDone;
      if (value) {
        buffer += decoder.decode(value, { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (line.startsWith("data:")) {
            const dataText = line.slice(5).trim();
            try {
              const parsed = JSON.parse(dataText);
              if (parsed.result || parsed.error) {
                responsePayload = parsed;
                await reader.cancel();
                done = true;
                break;
              }
            } catch (e) {
              // Ignore incomplete JSON chunks
            }
          }
        }
      }
    }
    if (!responsePayload) {
      throw new Error("No valid JSON-RPC payload received in event-stream");
    }
    return responsePayload;
  } else {
    return await res.json();
  }
}

// Low-level fetch payload calling the raw JSON-RPC stream
async function fetchMcpCall(toolName, args) {
  const start = Date.now();
  const reqId = state.requestIdCounter++;
  const requestPayload = {
    jsonrpc: "2.0",
    method: "tools/call",
    params: {
      name: toolName,
      arguments: args
    },
    id: reqId
  };

  setStatus("active", "Calling...");
  state.busy = true;

  try {
    const endpoint = "/mcp";
    const headers = {
      "Content-Type": "application/json",
      "X-Client-ID": $("clientIdInput")?.value || "mcp-ui-client",
      "X-Session-ID": $("sessionInput")?.value || "mcp-ui-session",
    };

    if (state.clientToken) {
      headers["Authorization"] = `Bearer ${state.clientToken}`;
    }

    const caller = $("callerInput")?.value.trim() || "";
    if (caller) {
      headers["X-Caller"] = caller;
    }

    let res = await fetch(endpoint, {
      method: "POST",
      headers: {
        ...headers,
        "Accept": "application/json, text/event-stream"
      },
      body: JSON.stringify(requestPayload)
    });

    if (res.status === 406) {
      console.warn("406 returned, retrying with Accept: application/json...");
      res = await fetch(endpoint, {
        method: "POST",
        headers: {
          ...headers,
          "Accept": "application/json"
        },
        body: JSON.stringify(requestPayload)
      });
    }

    const elapsed = Date.now() - start;
    setText("lastLatency", `${elapsed} ms`);

    if (res.status === 401 || res.status === 429) {
      const wizard = $("apiKeyWizard");
      if (wizard) wizard.classList.remove("hidden");
    } else {
      const wizard = $("apiKeyWizard");
      if (wizard) wizard.classList.add("hidden");
    }

    if (!res.ok) {
      const errText = await res.text();
      const errResponse = { error: { message: `HTTP ${res.status}: ${errText}` } };
      logRpcTransaction(requestPayload, errResponse, elapsed, true);
      throw new Error(`HTTP ${res.status}: ${errText}`);
    }

    let responsePayload = await parseMcpResponse(res);

    const isError = Boolean(responsePayload.error || responsePayload.result?.isError);
    logRpcTransaction(requestPayload, responsePayload, elapsed, isError);

    setStatus(isError ? "error" : "active", isError ? "Error" : "Done");
    state.busy = false;

    // The facts pane holds the agent routing receipt; background tool calls
    // (status, discovery, models, metrics) carry no such receipt and must not
    // clobber it, so only the agent call updates the pane. The pane is
    // diagnostics: if rendering it throws, the answer must still be returned
    // and displayed — never re-throw from here.
    if (toolName === "agent") {
      try {
        renderFactsPane(toolName, responsePayload, elapsed);
      } catch (paneError) {
        console.error("Receipt pane render failed:", paneError);
      }
    }

    return responsePayload;
  } catch (err) {
    setStatus("error", "Error");
    state.busy = false;
    throw err;
  }
}

function extractToolPayload(jsonRpcResponse) {
  const result = jsonRpcResponse?.result;
  if (!result || !result.content || result.content.length === 0) {
    return { response: "Empty tool response result." };
  }

  // FastMCP encapsulates output text
  const textContent = result.content[0].text;
  let parsed;
  try {
    // If it's a Pydantic serialized string, parse it
    parsed = JSON.parse(textContent);
  } catch {
    parsed = { response: textContent, text: textContent };
  }

  return parsed;
}

function renderFactsPane(method, response, elapsed) {
  setText("factMethod", method);
  setText("factLatency", `${elapsed}ms`);

  if (response.error) {
    const status = setText("factStatus", "ERROR");
    if (status) status.style.color = "var(--red)";
    return;
  }

  const payload = extractToolPayload(response) || {};
  // Status must reflect AgentResult.finish_reason too: credential/plane
  // failures return a normal tools/call payload (not result.isError), so a
  // green SUCCESS receipt would lie about the outcome.
  let statusLabel = "SUCCESS";
  let statusColor = "var(--teal)";
  if (response.result?.isError) {
    statusLabel = "TOOL ERROR";
    statusColor = "var(--red)";
  } else if (payload.finish_reason === "error") {
    statusLabel = "FAILED";
    statusColor = "var(--red)";
  } else if (payload.finish_reason === "fallback" || payload.degraded === true) {
    statusLabel = "DEGRADED";
    statusColor = "var(--orange)";
  }
  const status = setText("factStatus", statusLabel);
  if (status) status.style.color = statusColor;

  setText("factTokens", payload.tokens || "-");
  // The wire may deliver cost as a number or a serialized string; only a
  // finite value renders as currency. cost===0 is real (CLI subscription or
  // a free API turn) — never paint "-" which reads as "unknown".
  const rawCost = payload.cost_usd;
  const cost = typeof rawCost === "string" && rawCost.trim() !== "" ? Number(rawCost) : rawCost;
  const rawBilling = payload.billing_class || payload.routing?.billing_class || "";
  const billing = typeof rawBilling === "string" ? rawBilling.trim() : "";
  let costLabel = "-";
  if (typeof cost === "number" && Number.isFinite(cost)) {
    const isSubscription = billing.toLowerCase() === "subscription";
    costLabel = (cost === 0 && isSubscription)
      ? "Subscription"
      : `$${cost.toFixed(5)}`;
  }
  setText("factCost", costLabel);
  setText("factRoute", payload.route || "-");
  setText("factPlane", payload.plane || "-");
  setText("factBilling", billing || "-");
  setText("factRequestedPlane", payload.requested_plane || payload.routing?.requested_plane || "-");
  setText("factModel", payload.model || "-");
  setText("factSelection", routingLabel(payload.routing?.why_detail || payload.why || "-"));
  const finishReason = payload.finish_reason || "-";
  const finishEl = setText("factFinishReason", finishReason);
  if (finishEl) finishEl.style.color = finishReason !== "-" && finishReason !== "final_answer" ? "var(--red)" : "";
  const degradedEl = setText("factDegraded", payload.degraded === undefined ? "-" : String(payload.degraded));
  if (degradedEl) degradedEl.style.color = payload.degraded === true ? "var(--red)" : "";
  // Mode provenance: confirms a phoneword port dial actually changed the mode.
  setText("factRequestedMode", payload.requested_mode || "-");
  setText("factModeSource", payload.mode_source || "-");
  setText("factDialedPort", payload.dialed_port || "-");
}

async function callAgent(prompt) {
  const args = {
    prompt: prompt,
    mode: $("modeInput").value,
    session: $("sessionInput").value || "default",
    plane: $("planeInput").value,
    fallback_policy: $("fallbackPolicyInput").value,
  };

  const model = $("modelInput").value;
  if (model) args.model = model;

  const sysPrompt = $("systemPromptInput").value.trim();
  if (sysPrompt) args.system_prompt = sysPrompt;

  const workspaceContext = $("workspaceContextInput")?.value.trim();
  if (workspaceContext) {
    args.workspace_context = workspaceContext;
    const label = $("workspaceLabelInput")?.value.trim();
    if (label) args.workspace_label = label;
  }

  // Add user bubble
  addMessageBubble("user", prompt);

  try {
    const rawResponse = await fetchMcpCall("agent", args);
    if (rawResponse.error) {
      const detail = rawResponse.error.message || JSON.stringify(rawResponse.error);
      addMessageBubble("error", `Gateway rejected the call: ${detail}`);
      return;
    }
    const payload = extractToolPayload(rawResponse);
    if (rawResponse.result?.isError) {
      addMessageBubble("error", `Tool error: ${payload.response || payload.text || "unknown tool failure"}`);
      return;
    }
    const answer = payload.text || payload.response || "No response field returned.";
    // finish_reason=error is a completed tools/call with a failed agent turn
    // (e.g. credential-setup) — show it as an error bubble, not an agent answer.
    if (payload.finish_reason === "error") {
      addMessageBubble("error", answer);
      return;
    }

    addMessageBubble("agent", answer);
    renderCitations(payload.citations);
  } catch (err) {
    addMessageBubble("error", `Invocation failed: ${err.message}`);
  }
}

// Research mode returns sources in AgentResult.citations, separate from the
// answer text — render them as a footer so the grounding is verifiable.
function renderCitations(citations) {
  if (!Array.isArray(citations) || citations.length === 0) return;
  const container = $("conversation");
  if (!container) return;
  const footer = document.createElement("div");
  footer.className = "message-bubble msg-citations";
  const heading = document.createElement("strong");
  heading.textContent = `Sources (${citations.length})`;
  footer.appendChild(heading);
  const list = document.createElement("ol");
  for (const citation of citations) {
    const url = typeof citation === "string" ? citation : citation?.url || "";
    if (!url) continue;
    const item = document.createElement("li");
    const safe = sanitizeHref(url);
    if (safe) {
      const link = document.createElement("a");
      link.href = safe;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = url;
      item.appendChild(link);
    } else {
      item.textContent = url;
    }
    list.appendChild(item);
  }
  footer.appendChild(list);
  container.appendChild(footer);
  container.scrollTop = container.scrollHeight;
}

function addMessageBubble(sender, text) {
  const container = $("conversation");
  if (!container) {
    // Last resort on a broken page: never silently drop a message.
    console.error(`Transcript container missing; ${sender} message:`, text);
    return;
  }
  const bubble = document.createElement("div");
  bubble.className = `message-bubble msg-${sender}`;
  if (sender === "agent") {
    // Agent answers arrive as markdown (AgentResult.text); user, system, and
    // error bubbles stay plain text.
    renderMarkdownInto(bubble, text);
  } else {
    bubble.innerText = text;
  }

  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

// A per-load session id so two browsers do not silently share one server-side
// conversation history under the old fixed "console-session-1" default.
function genSessionId() {
  let token;
  try {
    token = crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  } catch {
    token = Math.random().toString(36).slice(2, 10);
  }
  return `console-${token}`;
}

// Clear History rotates to a fresh session so the next turn starts clean; the
// prior server-side session is retained under its own id, not deleted here.
function clearConversation() {
  const sessionInput = $("sessionInput");
  const previous = sessionInput?.value.trim();
  if (sessionInput) sessionInput.value = genSessionId();
  const conversation = $("conversation");
  if (conversation) conversation.innerHTML = "";
  addMessageBubble(
    "system",
    previous
      ? `Cleared the view and started session ${sessionInput.value}. The server still holds the prior session (${previous}) history.`
      : "Session started. Ready to execute prompts."
  );
}

async function verifyPlaygroundSetup() {
  if (state.busy) return;
  addMessageBubble("system", "Checking UniGrok setup without running a model…");
  try {
    const response = await fetchMcpCall("grok_mcp_status", {view: "json"});
    const payload = extractToolPayload(response);
    const credentials = payload.credential_planes || {};
    const api = credentials.api?.available ? "API ready" : "API unavailable";
    const cli = credentials.cli?.available ? "CLI subscription ready" : "CLI unavailable";
    const policy = String(credentials.policy || "unknown").replaceAll("_", " ");
    addMessageBubble("agent", `Setup verified. ${cli}; ${api}; routing policy: ${policy}. No inference turn was used.`);
  } catch (err) {
    addMessageBubble("system", `Setup check failed: ${err.message}`);
  }
}

function setupConsoleActions() {
  $("verifySetupBtn").addEventListener("click", verifyPlaygroundSetup);

  $("runSampleBtn").addEventListener("click", async function() {
    if (state.busy) return;
    const prompt = this.dataset.prompt;
    $("promptInput").value = prompt;
    $("promptInput").dispatchEvent(new Event("input"));
    await callAgent(prompt);
  });

  document.querySelectorAll(".prompt-preset").forEach((button) => {
    button.addEventListener("click", () => {
      $("promptInput").value = button.dataset.prompt || "";
      $("promptInput").dispatchEvent(new Event("input"));
      $("promptInput").focus();
    });
  });

  $("sendBtn").addEventListener("click", async () => {
    const prompt = $("promptInput").value.trim();
    if (state.busy) return;
    if (!prompt) {
      addMessageBubble("system", "Add a task, choose a preset, or use Verify Setup for a no-prompt health check.");
      $("promptInput").focus();
      return;
    }
    await callAgent(prompt);
  });

  $("clearBtn").addEventListener("click", clearConversation);

  // Copy MCP JSON-RPC Payload
  $("copyConsoleCallBtn").addEventListener("click", function() {
    const args = {
      prompt: $("promptInput").value.trim() || "Reply with exactly: UniGrok agent is ready.",
      mode: $("modeInput").value,
      plane: $("planeInput").value,
      fallback_policy: $("fallbackPolicyInput").value,
    };
    const model = $("modelInput").value;
    if (model) args.model = model;

    const payload = {
      jsonrpc: "2.0",
      method: "tools/call",
      params: {
        name: "agent",
        arguments: args
      },
      id: 1
    };
    copyTextToClipboard(JSON.stringify(payload, null, 2), this);
  });

  // Copy inspector facts
  $("copyFactsBtn").addEventListener("click", function() {
    const facts = {
      method: $("factMethod").innerText,
      status: $("factStatus").innerText,
      finish_reason: $("factFinishReason").innerText,
      degraded: $("factDegraded").innerText,
      tokens: $("factTokens").innerText,
      cost: $("factCost").innerText,
      latency: $("factLatency").innerText,
      route: $("factRoute").innerText,
      plane: $("factPlane").innerText,
      billing: $("factBilling").innerText,
      requested_plane: $("factRequestedPlane").innerText,
      model: $("factModel").innerText,
      selection: $("factSelection").innerText,
    };
    copyTextToClipboard(JSON.stringify(facts, null, 2), this);
  });

  $("clearLogsBtn").addEventListener("click", () => {
    $("rpcLogs").innerHTML = '<div class="log-placeholder">No RPC calls tracked in this session yet.</div>';
  });
}

// --- WebMCP Tool Registration ---
async function registerWebMcpTools() {
  const ctx = window.document?.modelContext || window.navigator?.modelContext;
  if (!ctx || typeof ctx.registerTool !== "function") return;

  try {
    await ctx.registerTool({
      name: "unigrok_ui_layout_get",
      description: "Returns the deterministic IDE layout state and crawlable Console regions.",
      inputSchema: { type: "object", properties: {} },
      execute: async () => ({
        content: [{
          type: "text",
          text: JSON.stringify({
            layout: JSON.parse($("consoleGrid")?.dataset.layout || "{}"),
            regions: [...document.querySelectorAll("[data-region]")].map((node) => node.dataset.region),
            activeTab: state.activeTab,
          }, null, 2),
        }],
      }),
    });

    const resultShapeInputSchema = {
      type: "object",
      properties: {
        tool_name: { type: "string", enum: ["agent", "chat", "grok_reflect", "generate_image"] }
      },
      required: ["tool_name"]
    };
    const getResultShapeExample = async ({ tool_name }) => {
      const schemaName = tool_name === "agent" ? "AgentResult" : tool_name === "chat" ? "ChatResult" : tool_name === "grok_reflect" ? "ReflectionResult" : tool_name === "generate_image" ? "MediaResult" : "AgentResult";
      const schema = resultShapeExamples[schemaName];
      return {
        content: [{
          type: "text",
          text: schema
            ? JSON.stringify({ authoritative: false, source: "ui_example", ...schema }, null, 2)
            : `Tool '${tool_name}' not found.`
        }]
      };
    };

    await ctx.registerTool({
      name: "get_result_shape_example",
      description: "Returns an illustrative result field map. Live MCP tools/list schemas are authoritative.",
      inputSchema: resultShapeInputSchema,
      execute: getResultShapeExample
    });

    await ctx.registerTool({
      name: "get_schema",
      description: "Deprecated compatibility alias. Returns a non-authoritative result example; live MCP tools/list schemas are authoritative.",
      inputSchema: resultShapeInputSchema,
      execute: getResultShapeExample
    });

    await ctx.registerTool({
      name: "example_call",
      description: "Returns a JSON template payload/example call for a given UniGrok mode.",
      inputSchema: {
        type: "object",
        properties: {
          mode: { type: "string", enum: ["auto", "fast", "reasoning", "thinking", "research"] }
        },
        required: ["mode"]
      },
      execute: async ({ mode }) => {
        const examples = {
          auto: { prompt: "Describe quantum computing.", mode: "auto" },
          fast: { prompt: "Hello!", mode: "fast" },
          reasoning: { prompt: "Design a relational database backup schema.", mode: "reasoning" },
          thinking: { prompt: "Perform deep multi-step verification of our endpoints.", mode: "thinking" },
          research: { prompt: "Compare WebMCP vs custom IETF discovery protocols.", mode: "research" }
        };
        return {
          content: [{
            type: "text",
            text: JSON.stringify(examples[mode] || {}, null, 2)
          }]
        };
      }
    });

    await ctx.registerTool({
      name: "simulate_reasoning_guard",
      description: "Inspects guard behavior against this release's bundled profile effort declarations.",
      inputSchema: {
        type: "object",
        properties: {
          model: { type: "string", enum: ["grok-build-0.1", "grok-4.3", "grok-4.5"] },
          required_level: { type: "string", enum: ["low", "medium", "high"] }
        },
        required: ["model", "required_level"]
      },
      execute: async ({ model, required_level }) => {
        const levels = { none: 0, low: 1, medium: 2, high: 3 };
        const modelLevels = {
          "grok-build-0.1": 0,
          "grok-4.3": 0,
          "grok-4.5": 0
        };
        const requiredWeight = levels[required_level];
        const modelWeight = modelLevels[model] || 0;

        if (modelWeight < requiredWeight) {
          return {
            content: [{
              type: "text",
              text: `ERROR: Model '${model}' has no declared reasoning_effort in this release's bundled profile (normalized weight ${modelWeight}), which fails the required guard threshold of '${required_level}' (${requiredWeight}). Pre-flight abort triggered!`
            }],
            isError: true
          };
        }

        return {
          content: [{
            type: "text",
            text: `SUCCESS: Model '${model}' has no declared reasoning_effort in this release's bundled profile (normalized weight ${modelWeight}) and satisfies required level '${required_level}' (weight ${requiredWeight}). Guard passed.`
          }]
        };
      }
    });

    await ctx.registerTool({
      name: "fetch_okf_bundle",
      description: "Returns the metadata, manifest, and topic URLs in the OKF bundle.",
      inputSchema: {
        type: "object",
        properties: {}
      },
      execute: async () => {
        const response = await fetch("/docs/okf/okf-manifest.json");
        if (!response.ok) throw new Error(`OKF manifest returned HTTP ${response.status}`);
        const source = await response.json();
        const manifest = {
          ...source,
          root: `/docs/okf/${source.root}`,
          files: source.files.map((fileName) => `/docs/okf/${fileName}`)
        };
        return {
          content: [{
            type: "text",
            text: JSON.stringify(manifest, null, 2)
          }]
        };
      }
    });
  } catch (err) {
    console.error("WebMCP tools registration failed: ", err);
  }
}

// --- Proactive Safety Checks & Handlers (v0.4.1) ---

function checkBrowserCompatibility() {
  const isChrome = /Chrome/.test(navigator.userAgent) && /Google Inc/.test(navigator.vendor);
  const card = $("browserWarningCard");
  if (!isChrome && card) {
    card.classList.remove("hidden");
  }
}

let isOffline = false;
const LIVE_UI_URL = "http://localhost:4765/ui/";

function isFilePreview() {
  return window.location.protocol === "file:";
}

// Detects the stale-cache skew that once discarded rendered answers: the page
// HTML and this script came from different releases. One reload revalidates
// the document (reloads bypass heuristic freshness for the main resource);
// the per-pair flag stops a loop if the server itself keeps serving the skew.
function enforceUiVersionHandshake() {
  const htmlVersion = document.querySelector('meta[name="unigrok-ui-version"]')?.content || "missing";
  if (htmlVersion === UI_ASSET_VERSION) return false;
  const flag = `unigrok.ui.reloaded.${htmlVersion}->${UI_ASSET_VERSION}`;
  try {
    if (!sessionStorage.getItem(flag)) {
      sessionStorage.setItem(flag, "1");
      window.location.reload();
      return true;
    }
  } catch (_) {
    // Storage blocked: fall through to the visible banner.
  }
  const message = $("offlineAlertMessage");
  const banner = $("dockerOfflineAlert");
  if (message && banner) {
    message.textContent = "⚠️ This page is out of date (browser cache). Hard refresh — Cmd/Ctrl+Shift+R — to load the current Console.";
    banner.classList.remove("hidden");
  }
  return true;
}

function renderFilePreviewNotice() {
  const alertBanner = $("dockerOfflineAlert");
  const message = $("offlineAlertMessage");
  const restartBtn = $("dockerRestartBtn");
  const fallback = $("restartManualFallback");
  if (!alertBanner || !message || !restartBtn) return;
  isOffline = false;
  message.textContent = "Preview only — use the live Console for runtime actions.";
  restartBtn.textContent = "Open Live UI";
  restartBtn.dataset.action = "open-live-ui";
  fallback?.classList.add("hidden");
  fallback?.style.setProperty("display", "none", "important");
  alertBanner.classList.add("preview-banner");
  alertBanner.classList.remove("hidden");
  setStatus("idle", "Preview");
}

async function pollReadyz() {
  if (isFilePreview()) {
    renderFilePreviewNotice();
    return;
  }
  let notReadyDetail = null;
  try {
    const res = await fetch("/readyz");
    if (res.ok) {
      gatewayReadiness.ready = true;
      gatewayReadiness.detail = null;
      if (isOffline) {
        isOffline = false;
        const alertBanner = $("dockerOfflineAlert");
        if (alertBanner) alertBanner.classList.add("hidden");
        await runStartupCheck();
        await fetchRuntimeStatus();
        await loadModelsList();
        await fetchMcpListTools();
      }
      return;
    }
    notReadyDetail = await describeNotReady(res);
  } catch (err) {
    // fall through: connection-level failure
  }
  isOffline = true;
  gatewayReadiness.ready = false;
  gatewayReadiness.detail = notReadyDetail;
  const message = $("offlineAlertMessage");
  if (message) {
    message.textContent = notReadyDetail
      ? `⚠️ ${notReadyDetail}`
      : "⚠️ Local UniGrok Gateway Offline! No connection detected.";
  }
  const alertBanner = $("dockerOfflineAlert");
  if (alertBanner) alertBanner.classList.remove("hidden");
  setStatus("error", notReadyDetail ? "Not Ready" : "Offline");
}

function setupDockerRestart() {
  const restartBtn = $("dockerRestartBtn");
  if (restartBtn) {
    restartBtn.addEventListener("click", async () => {
      if (isFilePreview() || restartBtn.dataset.action === "open-live-ui") {
        window.location.assign(LIVE_UI_URL);
        return;
      }
      const originalText = restartBtn.innerText;
      restartBtn.innerText = "Restarting...";
      restartBtn.disabled = true;
      try {
        const res = await fetchMcpCall("grok_mcp_restart_container", {});
        const payload = extractToolPayload(res);
        if (payload && payload.data && payload.data.status === "disabled") {
          const fallback = $("restartManualFallback");
          if (fallback) fallback.classList.remove("hidden");
          alert("Docker container restart is disabled on this server.\n\nRun manually: docker compose up --build -d");
        } else {
          alert("Docker container restart triggered! The server may take a few moments to boot back up.");
        }
      } catch (err) {
        console.error("Restart failed: ", err);
        const fallback = $("restartManualFallback");
        if (fallback) fallback.classList.remove("hidden");
        alert("Failed to trigger restart: " + err.message);
      } finally {
        restartBtn.innerText = originalText;
        restartBtn.disabled = false;
      }
    });
  }

  const copyManualBtn = $("copyManualRestartBtn");
  if (copyManualBtn) {
    copyManualBtn.addEventListener("click", () => {
      navigator.clipboard.writeText("docker compose up --build -d").then(() => {
        const originalText = copyManualBtn.innerText;
        copyManualBtn.innerText = "Copied!";
        setTimeout(() => {
          copyManualBtn.innerText = originalText;
        }, 1500);
      });
    });
  }

  // Check immediately, then poll the live HTTP origin every 5 seconds.
  pollReadyz();
  setInterval(pollReadyz, 5000);
}

function setupCredentialActions() {
  const copyButton = $("copyCredentialActionBtn");
  if (!copyButton) return;
  copyButton.addEventListener("click", async () => {
    const command = copyButton.dataset.command || "";
    if (!command) return;
    await navigator.clipboard.writeText(command);
    const original = copyButton.innerText;
    copyButton.innerText = "Copied";
    setTimeout(() => { copyButton.innerText = original; }, 1500);
  });
}

function setupApiKeyWizard() {
  const saveBtn = $("saveWizardTokenBtn");
  const tokenInput = $("wizardTokenInput");

  if (saveBtn && tokenInput) {
    tokenInput.value = state.clientToken;

    saveBtn.addEventListener("click", () => {
      const token = tokenInput.value.trim();
      state.clientToken = token;

      const originalText = saveBtn.innerText;
      saveBtn.innerText = "Saved!";
      setTimeout(() => {
        saveBtn.innerText = originalText;
        const wizard = $("apiKeyWizard");
        if (wizard) wizard.classList.add("hidden");
      }, 1500);

      setTimeout(async () => {
        await runStartupCheck();
        await fetchRuntimeStatus();
        await loadModelsList();
        await fetchMcpListTools();
      }, 100);
    });
  }
}

function setupCostEstimator() {
  const promptInput = $("promptInput");
  const estimator = $("costEstimator");
  const modelInput = $("modelInput");

  if (!promptInput || !estimator) return;

  const updateCost = () => {
    const text = promptInput.value;
    const estTokens = Math.ceil(text.length * 0.25);
    const isLarge = estTokens >= 100000;

    estimator.innerText = `Local input estimate: ~${estTokens.toLocaleString()} tokens • exact API cost is reported after execution`;

    if (isLarge) {
      estimator.classList.add("expensive");
    } else {
      estimator.classList.remove("expensive");
    }

    const budgetGuard = $("budgetGuardToggle");
    const sendBtn = $("sendBtn");
    if (budgetGuard && budgetGuard.checked && isLarge) {
      sendBtn.disabled = true;
      sendBtn.innerText = "Blocked: Task Too Large";
      sendBtn.style.opacity = "0.5";
    } else if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.innerText = "Run Task";
      sendBtn.style.opacity = "";
    }
  };

  promptInput.addEventListener("input", updateCost);
  if (modelInput) modelInput.addEventListener("change", updateCost);
  const budgetGuard = $("budgetGuardToggle");
  if (budgetGuard) budgetGuard.addEventListener("change", updateCost);
}

// --- Initializer ---
function init() {
  if (isFilePreview()) {
    // IDE artifact viewers may reopen this source file. Move immediately to
    // the server-backed UI; show the compact fallback only if navigation is
    // blocked by the host.
    setTimeout(renderFilePreviewNotice, 700);
    window.location.replace(LIVE_UI_URL);
    return;
  }
  // A version-skewed page is about to reload (or has been told to hard
  // refresh); wiring the rest of the UI against mismatched DOM is pointless.
  if (enforceUiVersionHandshake()) return;
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("data-region", panel.dataset.region || panel.id);
    const button = document.querySelector(`.nav-btn[data-tab="${panel.id}"]`);
    if (button) panel.setAttribute("aria-labelledby", button.id);
    panel.hidden = !panel.classList.contains("active");
  });
  setupLayoutController();
  setupTabRouter();
  // Advanced demos retired from Core Console; keep functions for optional dead panels.
  if ($("tab-schemas") && !$("tab-schemas").hidden) setupSchemaExplorer();
  if ($("tab-guard") && !$("tab-guard").hidden) setupReasoningGuard();
  if ($("tab-okf") && !$("tab-okf").hidden) setupOkfClipboard();
  // Proactive safety checks initialization
  checkBrowserCompatibility();
  setupDockerRestart();
  setupCredentialActions();
  setupMetricsControls();

  $("refreshModelsBtn")?.addEventListener("click", () => loadPlaneModelCatalog(true));

  // Onboarding actions
  $("copyDiscoverBtn").addEventListener("click", runDiscoverSelfOnboarding);
  $("copyQuickStartBtn")?.addEventListener("click", function () {
    copyTextToClipboard($("quickStartCommands")?.textContent || "", this);
  });
  $("copyEndpointBtn")?.addEventListener("click", function () {
    copyTextToClipboard(resolveMcpEndpoint(), this);
  });
  $("copyMcpJsonBtn")?.addEventListener("click", function () {
    copyTextToClipboard(genericMcpJson(resolveMcpEndpoint()), this);
  });
  $("copyAgentPromptBtn")?.addEventListener("click", function () {
    copyTextToClipboard(agentSetupPrompt(resolveMcpEndpoint()), this);
  });
  $("copyPrimaryActionBtn")?.addEventListener("click", function () {
    // Label and action stay aligned via syncPrimaryCta: install only when offline.
    if (this.dataset.cta === "install" || !$("quickStartCard")?.classList.contains("hidden")) {
      copyTextToClipboard($("quickStartCommands")?.textContent || "", this);
      return;
    }
    copyTextToClipboard(genericMcpJson(resolveMcpEndpoint()), this);
  });
  $("refreshSpendBtn")?.addEventListener("click", fetchLiveMetrics);
  renderConnectSnippets();
  $("setupRecheckBtn")?.addEventListener("click", async () => {
    const ready = await runStartupCheck();
    const runtime = await fetchRuntimeStatus();
    // runStartupCheck populated gatewayReadiness with the named failing checks;
    // keep that detail rather than falling back to the generic message.
    renderSetupStatus(runtime, ready, gatewayReadiness.detail);
  });

  // Telemetry refresh action
  $("refreshMetricsBtn").addEventListener("click", fetchLiveMetrics);

  $("planeInput")?.addEventListener("change", updatePlaneControls);
  updatePlaneControls();

  $("runWebMcpBridgeBtn")?.addEventListener("click", () => {
    checkWebMcpBridge();
    loadWebMcpManifest();
  });

  switchTab("tab-onboarding");

  window.parseMcpResponse = parseMcpResponse;
  window.fetchMcpListTools = fetchMcpListTools;

  setTimeout(async () => {
    const ready = await runStartupCheck();
    const runtime = await fetchRuntimeStatus();
    if (runtime?.credential_planes) {
      state.credentialPlanes = runtime.credential_planes;
    }
    renderSetupStatus(runtime, ready, gatewayReadiness.detail);
    switchTab("tab-onboarding");
    // Keep OpenAI-compat model list warm for any residual consumers; dual-plane
    // Planes catalog is lazy-loaded only when the Planes tab opens.
    await loadModelsList();
    await fetchMcpListTools();
    await fetchLiveMetrics();
    // WebMCP browser registration is optional/experimental; skip on Core glass.
  }, 100);
}

init();
