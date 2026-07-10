# src/server.py
# Thin FastMCP router importing decomposed tools

import logging
import os
import sys
from typing import Iterable, Optional

from mcp.server.fastmcp import FastMCP
from .utils import setup_logging, store, orchestrate

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
    "server-side web/X search and sandboxed code execution, plus local file, "
    "git, and test tools, and persistent session memory.\n\n"
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
    " Commit-anchored workspace memory: `recall_workspace_memory` returns "
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
    agent,
    chat,
    grok_agent,
    grok_reflect,
    stateful_chat,
    retrieve_stateful_response,
    delete_stateful_response,
    chat_with_vision,
    chat_with_files,
    register_chat_tools,
    GrokAgentInput,
    GrokReflectionResult,
)
from .tools.media import (
    generate_image,
    generate_video,
    extend_video,
    register_media_tools,
)
from .tools.system import (
    grok_mcp_status,
    grok_mcp_discover_self,
    list_chat_sessions,
    get_chat_history,
    clear_chat_history,
    list_models,
    list_models_detailed,
    xai_upload_file,
    xai_list_files,
    xai_get_file,
    xai_get_file_content,
    xai_delete_file,
    read_local_file,
    list_project_files,
    remote_code_execution,
    run_local_tests,
    web_search,
    x_search,
    register_system_tools,
    db_vacuum,
)
from .tools.git import (
    git_status,
    git_diff,
    git_log,
    git_show,
    git_current_branch,
    git_create_branch,
    git_apply_patch,
    git_commit,
    register_git_tools,
)
from .tools.research import (
    submit_research_job,
    get_research_job,
    list_research_jobs,
    register_research_tools,
)
from .tools.knowledge import (
    remember_fact,
    search_knowledge,
    forget_fact,
    distill_session,
    register_knowledge_tools,
)
from .tools.workspace_memory import (
    recall_workspace_memory,
    record_landed_outcome,
    explain_workspace_evidence,
    workspace_memory_status,
    sync_workspace_memory_notes,
    import_workspace_memory_notes,
    register_workspace_memory_tools,
)
from .tools.resources import register_resource_primitives

# Register all modules
register_chat_tools(mcp)
register_media_tools(mcp)
register_system_tools(mcp)
register_git_tools(mcp)
register_research_tools(mcp)
register_knowledge_tools(mcp)
register_workspace_memory_tools(mcp)
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
