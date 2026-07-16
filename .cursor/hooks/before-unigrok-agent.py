#!/usr/bin/env python3
"""beforeMCPExecution: tip when calling UniGrok `agent` (fail-open, never block)."""
from __future__ import annotations

import json
import sys


TIP = (
    "Cursor surface tip: rich UI → Canvas (.canvas.tsx); hive votes → "
    "plane=cli mode=fast index-diff; sponsor status → Ready/Live/Blocked."
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    # Matcher already filters, but keep fail-open for unexpected shapes.
    if "agent" not in tool.lower() and tool != "":
        sys.stdout.write(json.dumps({"permission": "allow"}))
        return 0

    sys.stdout.write(
        json.dumps(
            {
                "permission": "allow",
                "agent_message": TIP,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
