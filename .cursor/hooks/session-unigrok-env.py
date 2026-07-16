#!/usr/bin/env python3
"""sessionStart: set Cursor×UniGrok session env (fail-open).

Note: additional_context on sessionStart is documented but has known IDE race
bugs; env is the reliable channel for follow-on hooks.
"""
from __future__ import annotations

import json
import sys

def main() -> int:
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    out = {
        "env": {
            "UNIGROK_CURSOR_SURFACE": "canvas",
            "UNIGROK_CURSOR_CLIENT": "cursor",
        },
        # Best-effort; may be dropped by IDE race — env above is the durable bit.
        "additional_context": (
            "Cursor×UniGrok: prefer Canvas for rich UI beside chat; "
            "Mermaid for small diagrams; never raw HTML widgets. "
            "Hive polls: UniGrok agent plane=cli mode=fast index-diff. "
            "Human radio: Ready / Live / Blocked."
        ),
    }
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
