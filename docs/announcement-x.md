# X (Twitter) Announcement Thread

## Tweet 1 (Hook)
🚀 Launching UniGrok Control Center v0.4.0! 🚀

We just released a complete premium redesign of the UniGrok UI—a high-fidelity workbench for developers and agent swarms to test, audit, and monitor Grok 4.5/4.3 locally. 

Interactive diagnostics are now live! 👇 (1/4)

---

## Tweet 2 (Control Center Tabs)
What’s inside the v0.4.0 Control Center UI?
- **Quick Test Console**: Prompt the agent with selectable execution modes (auto, fast, reasoning, thinking, research).
- **Reasoning Guard Simulator**: Real-time playground to test pre-flight cognitive policy boundaries. (2/4)

---

## Tweet 3 (Documentation & telemetry)
Self-describing diagnostics:
- **OKF Browser**: Direct markdown explorer for Open Knowledge Format bundles.
- **WebMCP Inspector**: Client context and bridge probers.
- **Telemetry & RPC Wire Logs**: Live session tracking for cost, tokens, latency, and JSON-RPC wire frames. (3/4)

---

## Tweet 4 (Get Started)
We also patched client payload parameters (task -> prompt) for full schema compatibility!

Spin up the gateway locally:
```bash
git clone https://github.com/djtelicloud/grok-mcp-server.git
cd grok-mcp-server && docker compose up --build -d
```
Access the Control Center at: `http://localhost:8080/ui/` (4/4)
