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
from collections.abc import Collection, Mapping
from typing import Any, Optional

from .credentials import secret_environment_names


def scrubbed_subprocess_env(
    base: Optional[Mapping[str, str]] = None,
    *,
    allow_secret_names: Collection[str] = (),
) -> dict[str, str]:
    """Return a child environment without UniGrok server-owned secrets."""

    env = dict(os.environ if base is None else base)
    allowed = {str(name).upper() for name in allow_secret_names}
    for name in secret_environment_names(env):
        if name.upper() not in allowed:
            env.pop(name, None)
    return env


async def create_scrubbed_subprocess_exec(
    *program_and_args: str,
    env: Optional[Mapping[str, str]] = None,
    allow_secret_names: Collection[str] = (),
    **kwargs: Any,
) -> asyncio.subprocess.Process:
    """Launch an async child without inheriting server-owned secrets."""

    return await asyncio.create_subprocess_exec(
        *program_and_args,
        env=scrubbed_subprocess_env(
            env, allow_secret_names=allow_secret_names
        ),
        **kwargs,
    )


def scrubbed_subprocess_run(
    *popenargs: Any,
    env: Optional[Mapping[str, str]] = None,
    allow_secret_names: Collection[str] = (),
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run a synchronous child without inheriting server-owned secrets."""

    return subprocess.run(
        *popenargs,
        env=scrubbed_subprocess_env(
            env, allow_secret_names=allow_secret_names
        ),
        **kwargs,
    )
