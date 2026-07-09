const STORAGE_KEY = "unigrok.mcp.console.settings.v4";

const state = {
  activeTab: "tab-console",
  activeSchema: "AgentResult",
  activeOkfFile: "index.md",
  okfManifest: null,
  models: [],
  busy: false,
  requestIdCounter: 1,
  clientToken: "",
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
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tabId = btn.getAttribute("data-tab");
      switchTab(tabId);
    });
  });
}

function switchTab(tabId) {
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));

  const targetBtn = document.querySelector(`.nav-btn[data-tab="${tabId}"]`);
  const targetPanel = $(tabId);

  if (targetBtn && targetPanel) {
    targetBtn.classList.add("active");
    targetPanel.classList.add("active");
    state.activeTab = tabId;

    // Trigger tab-specific initializations
    if (tabId === "tab-schemas") {
      renderActiveSchema();
    } else if (tabId === "tab-okf" && !state.okfManifest) {
      loadOkfManifest();
    } else if (tabId === "tab-webmcp") {
      loadWebMcpManifest();
      checkWebMcpBridge();
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
      finish_reason: { type: "string", enum: ["final_answer", "fallback", "length", "error"] },
      cost_usd: { type: "number", description: "Aggregated execution cost in USD." },
      model: { type: "string", description: "ID of the Grok model serving the call." },
      profile: { type: "string", description: "Adapter hyperparams profile name." },
      tokens: { type: "integer", description: "Aggregated tokens consumed." },
      latency_sec: { type: "number", description: "Total request round-trip time." },
      route: { type: "string", enum: ["fast", "reasoning", "thinking", "research"] },
      plane: { type: "string", enum: ["API", "CLI-Fallback"] },
      citations: { type: "array", items: { type: "object", properties: { url: { type: "string" } } } },
      why: { type: "string", description: "Decision rationale metric." },
      degraded: { type: "boolean", description: "True if model fallback triggered." },
      trace: { type: "string", description: "Inner step reasoning traces." }
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
  $("rawMetricsReport").innerText = "Polling status metrics...";
  try {
    const res = await fetchMcpCall("grok_mcp_status", {});
    const payload = extractToolPayload(res);

    $("rawMetricsReport").innerText = JSON.stringify(payload, null, 2);

    // Naive regex parse of text status to populate grid chips
    const text = String(payload.text || payload.response || "");
    const costMatch = text.match(/Total Cost \(Developer API\):\s*`\$([\d\.]+)`/);
    const latencyMatch = text.match(/Average Query Latency:\s*`([\d\.]+)s`/);
    const splitMatch = text.match(/CLI vs API Routing Split:\s*`(\d+ CLI calls \/ \d+ API calls)`/);
    const breakerMatch = text.match(/Circuit Breakers:\s*`([^`]+)`/);

    if (costMatch) $("metricCost").innerText = `$${parseFloat(costMatch[1]).toFixed(5)}`;
    if (latencyMatch) $("metricLatency").innerText = `${latencyMatch[1]}s`;
    if (splitMatch) $("metricPlane").innerText = splitMatch[1].replace(" calls", "").replace(" calls", "");
    if (breakerMatch) $("metricBreaker").innerText = breakerMatch[1].split(" ")[0];
  } catch (err) {
    $("rawMetricsReport").innerText = `Failed to fetch telemetry report: ${err.message}`;
  }
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
    } else {
      throw new Error();
    }
  } catch {
    setStatus("error", "Offline");
  }
}

async function fetchRuntimeStatus() {
  try {
    const res = await fetch("/runtimez");
    if (!res.ok) throw new Error();
    const data = await res.json();
    $("runtimeChip").innerText = `runtime: ${data.runtime || "unknown"}`;
    $("transportChip").innerText = `transport: ${data.transport || "unknown"}`;
  } catch {
    $("runtimeChip").innerText = "runtime: unknown";
    $("transportChip").innerText = "transport: unknown";
  }
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
}

async function callAgent(prompt) {
  const args = {
    prompt: prompt,
    mode: $("modeInput").value,
    session: $("sessionInput").value || "default",
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

function setupConsoleActions() {
  $("sendBtn").addEventListener("click", async () => {
    const prompt = $("promptInput").value.trim();
    if (!prompt || state.busy) return;
    await callAgent(prompt);
  });

  $("clearBtn").addEventListener("click", resetConversation);

  // Copy MCP JSON-RPC Payload
  $("copyConsoleCallBtn").addEventListener("click", function() {
    const args = {
      prompt: $("promptInput").value.trim() || "Example task",
      mode: $("modeInput").value,
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
        const manifest = {
          okf_version: "0.1",
          name: "uni-grok-mcp",
          root: "/docs/okf/index.md",
          files: [
            "/docs/okf/index.md",
            "/docs/okf/agent-tool.md",
            "/docs/okf/chat-modes.md",
            "/docs/okf/reasoning-guard.md",
            "/docs/okf/grok-4.5-pinning.md",
            "/docs/okf/media-imagine.md",
            "/docs/okf/metrics-tool.md"
          ]
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

async function pollReadyz() {
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

  // Start polling every 5 seconds
  setInterval(pollReadyz, 5000);
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
    const model = modelInput ? modelInput.value : "";
    const isExpensive = model === "grok-4.5";

    const estTokens = Math.ceil(text.length * 0.25);
    const rate = isExpensive ? 0.015 : 0.00001;
    const cost = estTokens * rate;

    estimator.innerText = `Estimated Cost: $${cost.toFixed(5)} (${isExpensive ? 'grok-4.5 - HIGH!' : 'standard'})`;

    if (isExpensive) {
      estimator.classList.add("expensive");
    } else {
      estimator.classList.remove("expensive");
    }

    const budgetGuard = $("budgetGuardToggle");
    const sendBtn = $("sendBtn");
    if (budgetGuard && budgetGuard.checked && isExpensive && cost > 0.005) {
      sendBtn.disabled = true;
      sendBtn.innerText = "Blocked by Budget Guard";
      sendBtn.style.opacity = "0.5";
    } else if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.innerText = "Send to Agent";
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
  setupTabRouter();
  setupSchemaExplorer();
  setupReasoningGuard();
  setupOkfClipboard();
  setupConsoleActions();

  // Proactive safety checks initialization
  checkBrowserCompatibility();
  setupDockerRestart();
  setupApiKeyWizard();
  setupCostEstimator();

  // Onboarding action
  $("copyDiscoverBtn").addEventListener("click", runDiscoverSelfOnboarding);

  // Telemetry refresh action
  $("refreshMetricsBtn").addEventListener("click", fetchLiveMetrics);

  // Input listeners
  const inputIds = ["clientIdInput", "callerInput", "sessionInput", "modeInput", "modelInput", "systemPromptInput"];
  for (const id of inputIds) {
    const el = $(id);
    if (el) {
      el.addEventListener("input", updateActiveContext);
      el.addEventListener("change", updateActiveContext);
    }
  }

  resetConversation();

  window.parseMcpResponse = parseMcpResponse;
  window.fetchMcpListTools = fetchMcpListTools;

  setTimeout(async () => {
    await runStartupCheck();
    await fetchRuntimeStatus();
    await loadModelsList();
    await fetchMcpListTools();
    await registerWebMcpTools();
  }, 100);
}

init();
