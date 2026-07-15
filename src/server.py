# src/server.py
# Thin FastMCP router importing decomposed tools

import os
import sys
from typing import Iterable, Optional

from mcp.server.fastmcp import FastMCP
from .utils import setup_logging, store

from contextlib import asynccontextmanager

# Configure logging
logger = setup_logging()

@asynccontextmanager
async def server_lifespan(server):
    try:
        yield
    finally:
        from .utils import close_xai_client
        close_xai_client()
        await store.close()

# Initialize FastMCP Server instance
SERVER_INSTRUCTIONS = (
    "UniGrok is a unified Grok agent: one gateway to xAI's Grok models with "
    "server-side web/X search and sandboxed code execution, plus optional "
    "attached-workspace file, git, and test tools, and persistent session memory.\n\n"
    "Start with the `agent` tool — it is the headline entry point. Give it any "
    "nontrivial task and it auto-routes across Grok models, runs tools as "
    "needed, and returns structured execution metadata. Use `chat` for plain "
    "conversation when you want to pin a model, `chat_with_vision` for images, "
    "`chat_with_files` to ground answers on uploaded documents, and "
    "`grok_reflect` when you need a focused structured critique without a "
    "tool loop.\n\n"
    "Media: `generate_image`, `generate_video`, `extend_video` create with "
    "Grok Imagine. Cloud files: the `xai_*` tools manage uploads on xAI's "
    "servers, while `read_local_file`/`list_project_files` inspect the local "
    "workspace. Git: `git_status`/`git_diff`/`git_log`/`git_show` are "
    "read-only; write tools are gated behind local runtime flags. "
    "`web_search`/`x_search` answer real-time questions with citations, and "
    "`remote_code_execution` solves tasks in xAI's Python sandbox. "
    "Long-running research: `submit_research_job` starts a deferred background "
    "job (poll `get_research_job`), while `agent` mode='research' fans out "
    "multi-agent research inline with cited sources. Knowledge memory: "
    "`remember_fact`/`search_knowledge`/`forget_fact` manage durable distilled "
    "facts that auto-inject into matching prompts, and `distill_session` "
    "condenses a chat session into facts in the background."
    " Contributor-only commit-anchored workspace memory: `recall_workspace_memory` returns "
    "branch-relevant engineering evidence for a caller-supplied HEAD, while "
    "`record_landed_outcome` accepts evidence only for commits certified by "
    "the local `scripts/land` gate."
)

mcp = FastMCP(
    "UniGrok-MCP-Meta-Harness",
    instructions=SERVER_INSTRUCTIONS,
    lifespan=server_lifespan,
)

# Import modular tools for registration and public import exports (for backward compatibility / tests)
from .tools.chats import (
    register_chat_tools,
)
from .tools.media import (
    register_media_tools,
)
from .tools.system import (
    register_system_tools,
)
from .tools.git import (
    register_git_tools,
)
from .tools.research import (
    register_research_tools,
)
from .tools.knowledge import (
    register_knowledge_tools,
)
from .tools.workspace_memory import (
    register_workspace_memory_tools,
)
from .tools.resources import register_resource_primitives
from .tools.swarm import register_swarm_tools
from .tools.consistency import register_consistency_tools  # noqa: F401

# Register all modules
register_chat_tools(mcp)
register_media_tools(mcp)
register_system_tools(mcp)
register_git_tools(mcp)
register_research_tools(mcp)
register_knowledge_tools(mcp)
register_workspace_memory_tools(mcp)
register_swarm_tools(mcp)
register_consistency_tools(mcp)
register_resource_primitives(mcp)

def main(argv: Optional[Iterable[str]] = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    http_requested = (
        "--http" in argv
        or os.environ.get("UNIGROK_RUNTIME", "").lower() in ("cloudrun", "http")
        or os.environ.get("UNIGROK_HTTP", "").lower() in ("1", "true", "yes")
    )
    if http_requested:
        from .http_server import run_http_server

        run_http_server()
        return

    mcp.run()

if __name__ == "__main__":
    main()
