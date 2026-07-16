---
description: "Use when the user asks GitHub Copilot in VS Code to show HTML, render a visual artifact, display rich formatted output, or preview a chat result as HTML. Prefer HTML artifact files plus VS Code Integrated Browser over raw HTML in chat."
name: "Copilot HTML Artifacts"
---
# Copilot HTML artifacts

- In VS Code Copilot chat, prefer Markdown for inline responses. Do not assume raw HTML will render correctly in the chat surface.
- When the user wants real HTML output, create an `.html` artifact instead of forcing HTML into chat.
- For one-off visual output, place the artifact outside the repository unless the user explicitly asks for a tracked file.
- Tell the user to open the artifact with VS Code's **Open in Integrated Browser** for the most reliable in-IDE rendering path.
- If styling or interactivity matters, include the minimal CSS or JavaScript the artifact needs.
- If the user wants a reusable in-IDE application surface rather than a one-off artifact, recommend a VS Code webview or extension instead of chat HTML.
