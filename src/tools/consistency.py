# src/tools/consistency.py
# Consistency Radar tool for detecting architectural drift.

import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from ..utils import PathResolver, validate_local_input
from .chats import agent

logger = logging.getLogger("GrokMCP")

READONLY_TOOL = ToolAnnotations(readOnlyHint=True)


async def architecture_consistency_sweep(
    target_paths: List[str],
    rules_paths: List[str],
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """Perform a wide-pass consistency sweep across target code and rule documents.
    
    This tool reads the provided files, runs a deep reasoning pass using Grok,
    and returns a scored consistency report indicating where the codebase has
    drifted from the authoritative claims in the rules_paths.

    Args:
        target_paths: A list of paths to target source files to audit.
        rules_paths: A list of paths to authoritative documents (e.g. docs/ide-setup.md).
    """
    if not target_paths or not rules_paths:
        return {"error": "Input Validation Error: Must provide both target_paths and rules_paths."}

    workspace = PathResolver.get_workspace_root()
    context_blob = []
    
    # Read Rules
    context_blob.append("## AUTHORITATIVE RULES AND CLAIMS")
    for rp in rules_paths:
        path = workspace / rp
        try:
            safe_rp = validate_local_input(path, max_bytes=200_000)
            content = safe_rp.read_text(encoding="utf-8")
            context_blob.append(f"\n--- {rp} ---\n{content}")
        except Exception as e:
            return {"error": f"Failed to read rules file {rp}: {e}"}
            
    # Read Targets
    context_blob.append("\n\n## TARGET SOURCE CODE")
    for tp in target_paths:
        path = workspace / tp
        try:
            safe_tp = validate_local_input(path, max_bytes=200_000)
            content = safe_tp.read_text(encoding="utf-8")
            context_blob.append(f"\n--- {tp} ---\n{content}")
        except Exception as e:
            return {"error": f"Failed to read target file {tp}: {e}"}
            
    workspace_context = "\n".join(context_blob)
    
    prompt = (
        "You are the Antigravity 'Consistency Radar'. Your task is to perform a multi-hop "
        "consistency pass between the provided AUTHORITATIVE RULES AND CLAIMS and the TARGET SOURCE CODE.\n"
        "Look for:\n"
        "1. Claim vs. code drift (does the code actually do what the docs say?).\n"
        "2. Version or reference drift.\n"
        "3. Rule violations.\n\n"
        "Output a scored radar report (e.g. Consistency Score: X/100) and ranked 'narrow-PR' candidates "
        "describing exactly what needs to be changed in the code to align with the rules. "
        "Format as Markdown."
    )

    result_json = await agent(
        task=prompt,
        mode="reasoning",
        model=None,
        workspace_context=workspace_context,
        ctx=ctx,
    )
    
    # The agent function returns a JSON string, we parse it to a dict if it is one, or just return it.
    import json
    try:
        if isinstance(result_json, str):
            parsed = json.loads(result_json)
        else:
            parsed = result_json
        return {"status": "success", "report": parsed}
    except Exception:
        return {"status": "success", "report": result_json}


def register_consistency_tools(mcp: FastMCP):
    mcp.add_tool(architecture_consistency_sweep, annotations=READONLY_TOOL)
