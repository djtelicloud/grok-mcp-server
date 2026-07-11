const STORAGE_KEY = "unigrok.mcp.console.settings.v4";
const LAYOUT_KEY = "unigrok.mcp.console.layout.v2";
const LAYOUT_LIMITS = {
  nav: [148, 340],
  inspector: [210, 460],
  workbench: 300,
  workbenchComfort: 360,
  rail: 44,
  splitter: 4,
};

const defaultLayout = {
  navPresence: "show",
  inspectorPresence: "hide",
  navWidth: 200,
  inspectorWidth: 280,
  density: "auto",
};

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
  activeTab: "tab-console",
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
};

// --- DOM Selector Helper ---
const $ = (id) => document.getElementById(id);

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
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.classList.remove("active");
    b.setAttribute("aria-selected", "false");
    b.tabIndex = -1;
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.classList.remove("active");
    p.hidden = true;
  });

  const targetBtn = document.querySelector(`.nav-btn[data-tab="${tabId}"]`);
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
      loadPlaneModelCatalog();
    } else if (tabId === "tab-metrics") {
      fetchLiveMetrics();
    }
  }
}

// --- Hardcoded Pydantic schemas (from results.py) ---
const enforcedSchemas = {
  AgentResult: {
    description: "Enforced response model for unified agent actions.",
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
    required: ["response", "finish_reason", "cost_usd"]
  },
  ChatResult: {
    description: "Response model returned by completions (chat, stateful_chat).",
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
    required: ["response", "response_id"]
  },
  ReflectionResult: {
    description: "Critique review structure returned by grok_reflect.",
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
    required: ["ok", "critique"]
  },
  MediaResult: {
    description: "Enforced structure for Grok Imagine image and video tools.",
    properties: {
      response: { type: "string" },
      text: { type: "string" },
      images: { type: "array", items: { type: "string", description: "Generated image URLs." } },
      video_url: { type: "string" },
      duration_sec: { type: "number" },
      imagine_params: { type: "object", description: "Prompt parameters context for reproducibility." }
    },
    required: ["response"]
  },
  SystemResult: {
    description: "Payload format for search execution and system checks.",
    properties: {
      response: { type: "string" },
      text: { type: "string" },
      data: { type: "object", description: "Raw structured JSON outputs (citations, environment variables)." }
    },
    required: ["response"]
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
  const schema = enforcedSchemas[state.activeSchema];
  $("schemaNameTitle").innerText = `${state.activeSchema}.json`;
  if (schema) {
    $("schemaCodeBlock").innerText = JSON.stringify(schema, null, 2);
  } else {
    $("schemaCodeBlock").innerText = "Schema not found.";
  }
}

// --- Reasoning Guard Simulator ---
function setupReasoningGuard() {
  $("runGuardSimBtn").addEventListener("click", () => {
    const model = $("guardModel").value;
    const level = $("guardLevel").value;

    const weights = { none: 0, low: 1, medium: 2, high: 3 };
    const modelWeights = {
      "grok-build-0.1": 0,
      "grok-4.3": 2,
      "grok-4.5": 3,
    };

    const requiredWeight = weights[level];
    const modelWeight = modelWeights[model] || 0;

    const card = $("guardResultCard");
    card.className = "simulation-card";

    if (modelWeight < requiredWeight) {
      card.classList.add("block");
      $("simStatus").innerText = "BLOCKED BY GUARD";
      $("simExplanation").innerHTML = `<strong>Error:</strong> The target model <code>${model}</code> provides reasoning level weight <strong>${modelWeight}</strong>, which falls below your required threshold of <strong>'${level}'</strong> (weight ${requiredWeight}).<br><br>The gateway will throw a <code>ValueError</code> and abort the request before hitting the API.`;
    } else {
      card.classList.add("pass");
      $("simStatus").innerText = "PASS";
      $("simExplanation").innerHTML = `<strong>Success:</strong> Model <code>${model}</code> (weight <strong>${modelWeight}</strong>) satisfies the required guard threshold of <strong>'${level}'</strong> (weight ${requiredWeight}).<br><br>The orchestrator will safely route this call.`;
    }
  });
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
      const div = document.createElement("div");
      div.className = "okf-item" + (baseName === state.activeOkfFile ? " active" : "");
      div.innerText = baseName;
      div.addEventListener("click", () => {
        document.querySelectorAll(".okf-item").forEach((i) => i.classList.remove("active"));
        div.classList.add("active");
        state.activeOkfFile = baseName;
        loadOkfFile(baseName);
      });
      listContainer.appendChild(div);
    });

    loadOkfFile(state.activeOkfFile);
  } catch (err) {
    listContainer.innerHTML = `<div class="okf-item" style="border-color: var(--red); color: var(--red);">Failed: ${err.message}</div>`;
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
      viewer.innerHTML = "";
      viewer.appendChild(warningDiv);

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

    viewer.innerHTML = parseMarkdown(cleanText);
  } catch (err) {
    viewer.innerHTML = `<p style="color: var(--red);">Failed to render file: ${err.message}</p>`;
  }
}

// Simple, zero-dependency Markdown Formatter with XSS sanitization
function parseMarkdown(md) {
  let html = md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Headers
  html = html.replace(/^# (.*?)$/gm, "<h1>$1</h1>");
  html = html.replace(/^## (.*?)$/gm, "<h2>$1</h2>");
  html = html.replace(/^### (.*?)$/gm, "<h3>$1</h3>");

  // Bold / Code
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/`(.*?)`/g, "<code>$1</code>");

  // Code Blocks
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, "<pre><code class='$1'>$2</code></pre>");

  // Tables
  html = html.replace(/^\|(.*?)\|$/gm, (match, content) => {
    const cells = content.split("|").map(c => c.trim());
    const isHeader = html.indexOf("<table>") === -1;
    let row = "<tr>";
    cells.forEach(c => {
      if (c === "---" || c === ":---" || c === "---:") return;
      row += isHeader ? `<th>${c}</th>` : `<td>${c}</td>`;
    });
    row += "</tr>";
    return row;
  });

  // Wrap table rows in tables
  html = html.replace(/(<tr>[\s\S]*?<\/tr>)/g, "<table>$1</table>");
  html = html.replace(/<\/table>\s*<table>/g, "");

  // Unordered lists
  html = html.replace(/^\- (.*?)$/gm, "<li>$1</li>");
  html = html.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>");
  html = html.replace(/<\/ul>\s*<ul>/g, "");

  // Paragraphs (naive wrap)
  html = html.replace(/^(?!<h|<li|<ul|<ol|<table|<tr|<th|<td|<pre|<\/pre|<\/code|<code>)(.+)$/gm, "<p>$1</p>");

  // Sanitize XSS elements (dangerous attributes and protocol links)
  html = html
    .replace(/javascript:/gi, "no-javascript:")
    .replace(/on\w+\s*=/gi, "no-onclick=");

  return html;
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
  $("rawMetricsReport").innerText = "Polling structured MCP metrics...";
  $("metricsStatus").innerText = "Refreshing local usage ledger…";
  try {
    const res = await fetchMcpCall("grok_mcp_status", { view: "json" });
    const payload = extractToolPayload(res);
    if (!payload?.usage?.today || !payload?.usage?.lifetime) {
      throw new Error("MCP returned an unsupported metrics payload");
    }
    state.metricsSnapshot = payload;
    renderMetricsSnapshot();
  } catch (err) {
    $("rawMetricsReport").innerText = `Failed to fetch telemetry report: ${err.message}`;
    $("metricsStatus").innerText = "Metrics unavailable — the MCP status call failed.";
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
    const success = metrics.success_rate === null ? "—" : `${(metrics.success_rate * 100).toFixed(1)}%`;
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

  $("metricApiCost").innerText = `$${Number(summary.api_cost_usd || 0).toFixed(5)}`;
  $("metricRequests").innerText = Number(summary.requests || 0).toLocaleString();
  $("metricLatency").innerText = formatMetric(summary.avg_latency_sec, (v) => `${v.toFixed(2)}s`);
  $("metricSuccess").innerText = formatMetric(summary.success_rate, (v) => `${(v * 100).toFixed(1)}%`);
  $("metricPlane").innerText = `${apiRequests} / ${cliRequests}`;
  $("metricTokens").innerText = formatTokens(summary.tracked_tokens);

  const quality = payload.usage.data_quality;
  const periodLabel = state.metricsPeriod === "today" ? "today" : "across the local ledger";
  $("metricsCoverage").innerText = summary.requests
    ? `${summary.requests} request${summary.requests === 1 ? "" : "s"} ${periodLabel}. ${summary.routing_receipt_requests || 0} row${summary.routing_receipt_requests === 1 ? "" : "s"} include explainable routing receipts; older rows remain valid for cost, latency, and plane counts.`
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
  $("discoverSelfCode").innerText = "Calling discover_self...";
  try {
    const res = await fetchMcpCall("grok_mcp_discover_self", {});
    const payload = extractToolPayload(res);
    $("discoverSelfCode").innerText = JSON.stringify(payload, null, 2);
  } catch (err) {
    $("discoverSelfCode").innerText = `Error: ${err.message}`;
  }
}

// --- Quick Test Console / JSON-RPC Core ---
async function runStartupCheck() {
  try {
    const res = await fetch("/readyz");
    if (res.ok) {
      setStatus("active", "Live");
      return true;
    } else {
      throw new Error();
    }
  } catch {
    setStatus("error", "Offline");
    return false;
  }
}

function renderSetupStatus(data, ready = true) {
  const target = $("setupStatusSummary");
  if (!target) return;
  const contract = data?.credential_planes || {};
  const effective = contract.effective_plane || "none";
  const runtime = data?.runtime || "unknown";
  const notices = (contract.notices || []).filter((notice) => notice.prompt_user);
  const attention = notices.map((notice) => notice.message).join(" ");
  const title = document.createElement("strong");
  title.textContent = ready ? "Gateway is live." : "Gateway needs attention.";
  const detail = document.createElement("span");
  detail.textContent = ready
    ? `Runtime: ${runtime} · active credential plane: ${effective}.${attention ? ` ${attention}` : ""}`
    : "The live readiness check failed. Restart or inspect the runtime before running a task.";
  target.replaceChildren(title, document.createElement("br"), detail);
}

async function fetchRuntimeStatus() {
  try {
    const res = await fetch("/runtimez");
    if (!res.ok) throw new Error();
    const data = await res.json();
    $("runtimeChip").innerText = `runtime: ${data.runtime || "unknown"}`;
    $("transportChip").innerText = `transport: ${data.transport || "unknown"}`;
    renderCredentialPlanes(data.credential_planes || null);
    renderSetupStatus(data, true);
    return data;
  } catch {
    $("runtimeChip").innerText = "runtime: unknown";
    $("transportChip").innerText = "transport: unknown";
    $("planeChip").innerText = "plane: unknown";
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

  const notice = (contract.notices || []).find((item) => item.prompt_user);
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
      "X-Client-ID": $("clientIdInput").value || "mcp-ui-client",
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

    const select = $("modelInput");
    select.innerHTML = '<option value="">auto (Recommended)</option>';
    state.models.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.innerText = m.id;
      select.appendChild(opt);
    });
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

function updatePlaneControls() {
  const plane = $("planeInput")?.value || "auto";
  const hint = $("planeHint");
  const fallback = $("fallbackPolicyInput");
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
  syncModelOptions();
}

function readableCatalogSource(source) {
  const labels = {
    grok_cli: "Live Grok CLI",
    "cli-fallback": "Fallback list",
    "cloudrun-disabled": "Unavailable in Cloud Run",
    xai_api: "Live xAI API",
    xai_api_fallback: "Fallback list",
    skipped: "Not queried",
  };
  return labels[source] || String(source || "Unknown").replaceAll("_", " ");
}

function renderPlaneModels(planeName, plane, sharedIds) {
  const prefix = planeName === "CLI" ? "cli" : "api";
  const stateChip = $(`${prefix}ModelPlaneState`);
  const source = $(`${prefix}ModelSource`);
  const economics = $(`${prefix}ModelEconomics`);
  const list = $(`${prefix}ModelList`);
  const models = Array.isArray(plane?.models) ? plane.models : [];

  const planeState = plane?.available
    ? "Ready"
    : plane?.credential_available && !plane?.catalog_available
      ? "Catalog fallback"
      : String(plane?.credential_state || "Unavailable").replaceAll("_", " ");
  stateChip.innerText = planeState;
  stateChip.className = `state-chip ${plane?.available ? "ready" : ""}`;
  source.innerText = readableCatalogSource(plane?.source);
  economics.innerText = plane?.economics || "Usage terms unavailable.";
  if (prefix === "cli") {
    $("cliDefaultModel").innerText = plane?.default_model || "Not reported";
  }

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
    card.className = "provider-model-card";

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
    copyButton.setAttribute("aria-label", `Copy ${id} model pin`);
    copyButton.addEventListener("click", () => copyTextToClipboard(id, copyButton));

    card.append(identity, copyButton);
    list.appendChild(card);
  }
}

function renderPlaneModelCatalog(catalog) {
  const routing = catalog?.routing || {};
  const planes = catalog?.planes || {};
  const sharedIds = new Set(catalog?.shared_model_ids || []);
  state.modelCatalog = catalog;
  syncModelOptions();

  $("modelsRoutingPolicy").innerText = String(routing.policy || "unknown").replaceAll("_", " ");
  $("modelsPreferredPlane").innerText = `Preferred: ${routing.preferred_plane || "none"}`;
  $("modelsEffectivePlane").innerText = `Effective now: ${routing.effective_plane || "none"}`;
  $("modelsRoutingRule").innerText = routing.rule || "Model and credential-plane selection are separate routing decisions.";

  renderPlaneModels("CLI", planes.CLI || {}, sharedIds);
  renderPlaneModels("API", planes.API || {}, sharedIds);

  const total = (planes.CLI?.models?.length || 0) + (planes.API?.models?.length || 0);
  const generated = catalog?.generated_at ? new Date(catalog.generated_at).toLocaleString() : "just now";
  $("modelsStatus").innerText = `${total} plane-specific model entr${total === 1 ? "y" : "ies"} • refreshed ${generated}`;

  const sharedNote = $("sharedModelsNote");
  if (sharedIds.size) {
    sharedNote.classList.remove("hidden");
    sharedNote.innerText = `${[...sharedIds].join(", ")} ${sharedIds.size === 1 ? "exists" : "exist"} on both planes. These are shown twice intentionally because authentication, availability, and usage accounting differ.`;
  } else {
    sharedNote.classList.add("hidden");
    sharedNote.innerText = "";
  }

  const warningList = $("modelCatalogWarnings");
  warningList.replaceChildren();
  const warnings = Array.isArray(catalog?.warnings) ? catalog.warnings : [];
  warningList.classList.toggle("hidden", warnings.length === 0);
  for (const warning of warnings) {
    const item = document.createElement("p");
    item.innerText = warning;
    warningList.appendChild(item);
  }
}

async function loadPlaneModelCatalog() {
  const refreshButton = $("refreshModelsBtn");
  if (refreshButton) refreshButton.disabled = true;
  $("modelsStatus").innerText = "Refreshing live CLI and API catalogs through MCP discovery…";
  try {
    const res = await fetchMcpCall("grok_mcp_discover_self", { include_models: true });
    const payload = extractToolPayload(res);
    const catalog = payload?.data?.model_catalog;
    if (!catalog) throw new Error("Discovery response did not include a model catalog.");
    renderPlaneModelCatalog(catalog);
  } catch (err) {
    $("modelsStatus").innerText = `Model catalog unavailable: ${err.message}`;
  } finally {
    if (refreshButton) refreshButton.disabled = false;
  }
}

function updateActiveContext() {
  const ac = $("activeClient");
  const as = $("activeSession");
  const am = $("activeMode");
  if (ac) ac.innerText = `client: ${$("clientIdInput").value || "default"}`;
  if (as) as.innerText = `session: ${$("sessionInput").value || "default"}`;
  if (am) am.innerText = `mode: ${$("modeInput").value || "auto"}`;
}

function setStatus(kind, label) {
  const pill = $("connectionState");
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
      "X-Client-ID": $("clientIdInput").value || "mcp-ui-client",
      "X-Session-ID": $("sessionInput").value || "mcp-ui-session",
    };

    if (state.clientToken) {
      headers["Authorization"] = `Bearer ${state.clientToken}`;
    }

    const caller = $("callerInput").value.trim();
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
    $("lastLatency").innerText = `${elapsed} ms`;

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

    setStatus("active", "Done");
    state.busy = false;

    // Render result details on right facts pane
    renderFactsPane(toolName, responsePayload, elapsed);

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

  // Legacy mode mapping
  if ($("legacyModeToggle") && $("legacyModeToggle").checked) {
    return {
      response: parsed.response || parsed.text || textContent,
      legacy: true
    };
  }
  return parsed;
}

function renderFactsPane(method, response, elapsed) {
  $("factMethod").innerText = method;
  $("factLatency").innerText = `${elapsed}ms`;

  if (response.error) {
    $("factStatus").innerText = "ERROR";
    $("factStatus").style.color = "var(--red)";
    return;
  }

  $("factStatus").innerText = "SUCCESS";
  $("factStatus").style.color = "var(--teal)";

  const payload = extractToolPayload(response);
  $("factTokens").innerText = payload.tokens || "-";
  $("factCost").innerText = payload.cost_usd ? `$${payload.cost_usd.toFixed(5)}` : "-";
  $("factRoute").innerText = payload.route || "-";
  $("factPlane").innerText = payload.plane || "-";
  $("factBilling").innerText = payload.billing_class || payload.routing?.billing_class || "-";
  $("factRequestedPlane").innerText = payload.requested_plane || payload.routing?.requested_plane || "-";
  $("factModel").innerText = payload.model || "-";
  $("factSelection").innerText = routingLabel(payload.routing?.why_detail || payload.why || "-");
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

  // Add user bubble
  addMessageBubble("user", prompt);

  try {
    const rawResponse = await fetchMcpCall("agent", args);
    const payload = extractToolPayload(rawResponse);

    const answer = payload.text || payload.response || "No response field returned.";
    addMessageBubble("agent", answer);
  } catch (err) {
    addMessageBubble("system", `Invocation failed: ${err.message}`);
  }
}

function addMessageBubble(sender, text) {
  const container = $("conversation");
  const bubble = document.createElement("div");
  bubble.className = `message-bubble msg-${sender}`;
  bubble.innerText = text;

  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

function resetConversation() {
  $("conversation").innerHTML = "";
  addMessageBubble("system", "Session started. Ready to execute prompts.");
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

  $("clearBtn").addEventListener("click", resetConversation);

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
      description: "Returns the deterministic IDE layout state and crawlable Control Center regions.",
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

    await ctx.registerTool({
      name: "get_schema",
      description: "Returns the Pydantic/JSON schema of a given UniGrok tool.",
      inputSchema: {
        type: "object",
        properties: {
          tool_name: { type: "string", enum: ["agent", "chat", "grok_reflect", "generate_image"] }
        },
        required: ["tool_name"]
      },
      execute: async ({ tool_name }) => {
        const schemaName = tool_name === "agent" ? "AgentResult" : tool_name === "chat" ? "ChatResult" : tool_name === "grok_reflect" ? "ReflectionResult" : tool_name === "generate_image" ? "MediaResult" : "AgentResult";
        const schema = enforcedSchemas[schemaName];
        return {
          content: [{
            type: "text",
            text: schema ? JSON.stringify(schema, null, 2) : `Tool '${tool_name}' not found.`
          }]
        };
      }
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
          auto: { task: "Describe quantum computing.", mode: "auto" },
          fast: { prompt: "Hello!", enable_agentic: false },
          reasoning: { task: "Design a relational database backup schema.", mode: "reasoning" },
          thinking: { task: "Perform deep multi-step verification of our endpoints.", mode: "thinking" },
          research: { task: "Compare WebMCP vs custom IETF discovery protocols.", mode: "research" }
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
      description: "Simulates checking if a model meets the required reasoning level.",
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
          "grok-4.3": 2,
          "grok-4.5": 3
        };
        const requiredWeight = levels[required_level];
        const modelWeight = modelLevels[model] || 0;

        if (modelWeight < requiredWeight) {
          return {
            content: [{
              type: "text",
              text: `ERROR: Model '${model}' has reasoning level weight ${modelWeight}, which fails the required guard threshold of '${required_level}' (${requiredWeight}). Pre-flight abort triggered!`
            }],
            isError: true
          };
        }

        return {
          content: [{
            type: "text",
            text: `SUCCESS: Model '${model}' (weight ${modelWeight}) satisfies required reasoning level '${required_level}' (weight ${requiredWeight}). Guard passed.`
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

function renderFilePreviewNotice() {
  const alertBanner = $("dockerOfflineAlert");
  const message = $("offlineAlertMessage");
  const restartBtn = $("dockerRestartBtn");
  const fallback = $("restartManualFallback");
  if (!alertBanner || !message || !restartBtn) return;
  isOffline = false;
  message.textContent = "Preview only — use the live Control Center for runtime actions.";
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
  try {
    const res = await fetch("/readyz");
    if (res.ok) {
      if (isOffline) {
        isOffline = false;
        const alertBanner = $("dockerOfflineAlert");
        if (alertBanner) alertBanner.classList.add("hidden");
        await runStartupCheck();
        await fetchRuntimeStatus();
        await loadModelsList();
        await fetchMcpListTools();
      }
    } else {
      throw new Error();
    }
  } catch (err) {
    isOffline = true;
    const alertBanner = $("dockerOfflineAlert");
    if (alertBanner) alertBanner.classList.remove("hidden");
    setStatus("error", "Offline");
  }
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
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("data-region", panel.dataset.region || panel.id);
    const button = document.querySelector(`.nav-btn[data-tab="${panel.id}"]`);
    if (button) panel.setAttribute("aria-labelledby", button.id);
    panel.hidden = !panel.classList.contains("active");
  });
  setupLayoutController();
  setupTabRouter();
  setupSchemaExplorer();
  setupReasoningGuard();
  setupOkfClipboard();
  setupConsoleActions();

  // Proactive safety checks initialization
  checkBrowserCompatibility();
  setupDockerRestart();
  setupCredentialActions();
  setupApiKeyWizard();
  setupCostEstimator();
  setupMetricsControls();

  $("refreshModelsBtn").addEventListener("click", loadPlaneModelCatalog);

  // Onboarding action
  $("copyDiscoverBtn").addEventListener("click", runDiscoverSelfOnboarding);
  $("setupRecheckBtn")?.addEventListener("click", async () => {
    const ready = await runStartupCheck();
    const runtime = await fetchRuntimeStatus();
    renderSetupStatus(runtime, ready);
  });

  // Telemetry refresh action
  $("refreshMetricsBtn").addEventListener("click", fetchLiveMetrics);

  // Input listeners
  const inputIds = ["clientIdInput", "callerInput", "sessionInput", "modeInput", "modelInput", "planeInput", "fallbackPolicyInput", "systemPromptInput"];
  for (const id of inputIds) {
    const el = $(id);
    if (el) {
      el.addEventListener("input", updateActiveContext);
      el.addEventListener("change", updateActiveContext);
    }
  }
  $("planeInput")?.addEventListener("change", updatePlaneControls);
  updatePlaneControls();

  resetConversation();

  window.parseMcpResponse = parseMcpResponse;
  window.fetchMcpListTools = fetchMcpListTools;

  setTimeout(async () => {
    const ready = await runStartupCheck();
    const runtime = await fetchRuntimeStatus();
    renderSetupStatus(runtime, ready);
    if (!ready) switchTab("tab-onboarding");
    await loadModelsList();
    await loadPlaneModelCatalog();
    await fetchMcpListTools();
    await registerWebMcpTools();
  }, 100);
}

init();
