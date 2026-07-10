# X (Twitter) Announcement Thread — Public Launch (v0.4.1)

Media: attach `docs/media/grok-imagine-arch-motion.mp4` (Grok Imagine motion
render of the UniGrok architecture) to Tweet 1, and the README GIF
(`assets/control-center-demo.gif`) to Tweet 3.

## Tweet 1 (Hook)
🚀 UniGrok is now open source!

One local Grok server. Every IDE. Zero pasted API keys.

Claude Code, VS Code, Codex & Claude Desktop all sharing ONE @xai Grok agent —
with dual-plane routing that uses your Grok subscription to slash API costs.

⭐ https://github.com/djtelicloud/grok-mcp-server (1/4)

---

## Tweet 2 (The killer feature)
Why dual-plane?

🔵 API plane: metered xAI API (grok-4.5, grok-build-0.1)
🟣 CLI plane: your grok.com subscription — 512k-context grok-build at ~$0
marginal cost

UniGrok self-routes between them per request. Your XAI_API_KEY never leaves
the server. (2/4)

---

## Tweet 3 (Control Center)
Every agent call reports cost, tokens, latency, route & plane in real time.

The built-in Control Center gives you a Quick Test Console, Reasoning Guard
simulator, OKF docs browser, WebMCP inspector, and raw JSON-RPC wire logs —
all local, all free. (3/4)

---

## Tweet 4 (Get Started)
Spin it up in 60 seconds:

```bash
git clone https://github.com/djtelicloud/grok-mcp-server.git
cd grok-mcp-server && docker compose up --build -d
```

Control Center: http://localhost:4765/ui/
Docs, IDE configs & release notes in the repo. MIT licensed. (4/4)
