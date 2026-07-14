"""Strict identifier validation for values embedded in validator commands.

The campaign workflow interpolates caller-supplied values (run stamp,
campaign ID, dataset IDs, packet path) into the exact CLI commands agents
are told to run. Every such value must match a strict allowlist *before*
any command string is built, in the workflow runtime and again here at the
CLI boundary, so a hostile value can never smuggle shell syntax, path
traversal, or option injection into a validator invocation.
"""

from __future__ import annotations

import re

# run_stamp, campaign IDs, dataset IDs: single token, no shell metacharacters,
# no whitespace, no leading '-' (option injection), bounded length.
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Packet paths: POSIX-style relative or absolute paths from a fixed charset.
# No whitespace, no shell metacharacters, no leading '-'.
SAFE_PATH = re.compile(r"^/?[A-Za-z0-9._][A-Za-z0-9._/-]*$")


class IdentifierError(ValueError):
    """A caller-supplied identifier failed strict validation."""


def validate_identifier(name: str, value: str) -> str:
    """Return ``value`` when it is a safe single token; raise otherwise."""
    if not isinstance(value, str) or not SAFE_IDENTIFIER.match(value):
        raise IdentifierError(
            f"{name} {value!r} rejected: must match {SAFE_IDENTIFIER.pattern} "
            "(single token, no shell syntax, no whitespace, no leading '-')"
        )
    return value


def validate_packet_path(value: str) -> str:
    """Return ``value`` when it is a safe packet path; raise otherwise."""
    if not isinstance(value, str) or not SAFE_PATH.match(value):
        raise IdentifierError(
            f"packet path {value!r} rejected: must match {SAFE_PATH.pattern} "
            "(no shell syntax, no whitespace, no leading '-')"
        )
    parts = [p for p in value.split("/") if p]
    if ".." in parts:
        raise IdentifierError(
            f"packet path {value!r} rejected: '..' traversal is not allowed"
        )
    return value
