"""Command-line entry point for UniGrok MCP."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, TextIO

from dotenv import load_dotenv

PLACEHOLDER_API_KEY = "your_xai_api_key_here"
DEFAULT_ENV_TEMPLATE = """# Template only - fill in real values before real Grok calls.
XAI_API_KEY=your_xai_api_key_here
UNIGROK_RUNTIME=local
UNIGROK_API_KEYS=
UNIGROK_STATE_DIR=
ENABLE_GIT_WRITE=0
"""


def _project_root() -> Path:
    explicit = os.environ.get("UNIGROK_PROJECT_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "pyproject.toml").is_file():
        return source_root

    # Installed wheels live under site-packages. User-owned first-run files
    # belong in the directory where the command is invoked, not in the
    # interpreter environment.
    return Path.cwd().resolve()


def _http_requested(argv: Iterable[str]) -> bool:
    # Mirrors src.server.main()'s transport selection.
    return (
        "--http" in argv
        or os.environ.get("UNIGROK_RUNTIME", "").lower() in ("cloudrun", "http")
        or os.environ.get("UNIGROK_HTTP", "").lower() in ("1", "true", "yes")
    )


def _grok_cli_auth_ready() -> bool:
    return bool(shutil.which("grok")) and (Path.home() / ".grok" / "auth.json").exists()


def _write_init_config(stream: TextIO, root: Path) -> None:
    endpoint = "http://localhost:4765/mcp"
    print("", file=stream)
    print("Shared HTTP endpoint", file=stream)
    print(f"  {endpoint}", file=stream)
    print("", file=stream)
    print("Start the shared service", file=stream)
    print(f"  cd {root}", file=stream)
    print("  docker compose up --build -d", file=stream)
    print("  curl --fail -s http://localhost:4765/healthz", file=stream)
    print("After configuring API or CLI credentials", file=stream)
    print("  curl --fail -s http://localhost:4765/readyz", file=stream)
    print("", file=stream)
    print("VS Code (.vscode/mcp.json or user mcp.json)", file=stream)
    print("""{
  "servers": {
    "unigrok": {
      "type": "http",
      "url": "http://localhost:4765/mcp",
      "headers": { "X-Client-ID": "vscode" }
    }
  }
}""", file=stream)
    print("", file=stream)
    print("Claude Desktop (claude_desktop_config.json via mcp-remote)", file=stream)
    print("""{
  "mcpServers": {
    "unigrok": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "http://localhost:4765/mcp",
        "--header", "X-Client-ID: claude-desktop"
      ]
    }
  }
}""", file=stream)
    print("", file=stream)
    print("Claude Code", file=stream)
    print(
        "  claude mcp add --transport http unigrok http://localhost:4765/mcp "
        '--header "X-Client-ID: claude-code"',
        file=stream,
    )
    print("", file=stream)
    print("Codex (~/.codex/config.toml)", file=stream)
    print("""[mcp_servers.grok]
url = "http://localhost:4765/mcp"
http_headers = { "X-Client-ID" = "codex" }""", file=stream)
    print("", file=stream)
    print(
        "If UNIGROK_API_KEYS is set in .env, add Authorization: Bearer <token> "
        "to each client header block.",
        file=stream,
    )


def init_project(root: Path | None = None, stream: TextIO | None = None) -> int:
    """Create first-run files and print IDE setup snippets."""
    stream = stream or sys.stdout
    root = (root or _project_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)

    env_path = root / ".env"
    example_path = root / "example.env"
    packaged_example_path = Path(__file__).resolve().parents[1] / "example.env"
    if env_path.exists():
        print(f".env already exists at {env_path}; leaving it unchanged.", file=stream)
    elif example_path.exists():
        shutil.copyfile(example_path, env_path)
        print(f"Created {env_path} from {example_path}.", file=stream)
    elif packaged_example_path.exists():
        shutil.copyfile(packaged_example_path, env_path)
        print(f"Created {env_path} from the packaged environment template.", file=stream)
    else:
        env_path.write_text(DEFAULT_ENV_TEMPLATE, encoding="utf-8")
        print(f"Created {env_path} from the built-in template.", file=stream)

    print("Edit .env and set XAI_API_KEY before making real xAI API calls.", file=stream)
    _write_init_config(stream, root)
    return 0


def main(argv: list[str] | None = None) -> int | None:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = _project_root()
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    if argv and argv[0] == "init":
        return init_project(root)

    if argv and argv[0] == "rag":
        from src.rag import rag_cli

        return rag_cli(argv[1:])

    if argv and argv[0] == "memory":
        from src.workspace_memory import workspace_memory_cli

        return workspace_memory_cli(argv[1:])

    from src import server

    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key or api_key == PLACEHOLDER_API_KEY:
        print("=" * 64, file=sys.stderr)
        if _grok_cli_auth_ready():
            print(
                "NOTICE: XAI_API_KEY is not set; using authenticated Grok CLI plane for MCP agent calls.",
                file=sys.stderr,
            )
            print("OpenAI-compatible /v1 API facade calls still need XAI_API_KEY.", file=sys.stderr)
        else:
            print("ERROR: XAI_API_KEY is missing and Grok CLI auth is not visible.", file=sys.stderr)
            print("Set XAI_API_KEY or make the grok CLI auth state available to this runtime.", file=sys.stderr)
        print("=" * 64, file=sys.stderr)
        mode = "HTTP" if _http_requested(argv) else "stdio"
        print(f"Continuing in {mode} mode.", file=sys.stderr)
    else:
        print("XAI_API_KEY found", file=sys.stderr)

    print("Started Grok MCP server", file=sys.stderr)
    server.main(argv)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
