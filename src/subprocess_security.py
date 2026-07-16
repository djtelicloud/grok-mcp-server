"""Credential-safe subprocess helpers.

UniGrok owns provider, management, and gateway credentials in its server
process. Child processes receive the caller-supplied environment after those
server-owned values are removed, or a scrubbed copy of the process environment
when no explicit environment is supplied.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Mapping
from typing import Any, Optional

from .credentials import SERVER_OWNED_SECRET_ENV_NAMES


def scrubbed_subprocess_env(
    base: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Return a child environment without UniGrok server-owned secrets."""

    env = dict(os.environ if base is None else base)
    for name in SERVER_OWNED_SECRET_ENV_NAMES:
        env.pop(name, None)
    return env


async def create_scrubbed_subprocess_exec(
    *program_and_args: str,
    env: Optional[Mapping[str, str]] = None,
    **kwargs: Any,
) -> asyncio.subprocess.Process:
    """Launch an async child without inheriting server-owned secrets."""

    return await asyncio.create_subprocess_exec(
        *program_and_args,
        env=scrubbed_subprocess_env(env),
        **kwargs,
    )


def scrubbed_subprocess_run(
    *popenargs: Any,
    env: Optional[Mapping[str, str]] = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run a synchronous child without inheriting server-owned secrets."""

    return subprocess.run(
        *popenargs,
        env=scrubbed_subprocess_env(env),
        **kwargs,
    )
