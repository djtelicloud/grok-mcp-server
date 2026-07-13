#!/usr/bin/env python3
"""Validate honest human and AI attribution on a bounded commit range."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


AGENT_TRAILERS = frozenset({"agent-assisted-by", "agent-reviewed-by"})
REQUIRED_CREDIT_FIELDS = frozenset({"model", "model-source", "role", "surface"})
OPTIONAL_CREDIT_FIELDS = frozenset({"evidence"})
MAILBOX_RE = re.compile(r"^([^<>\r\n]+?)\s*<([^<>\s]+)>$")
ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
MAX_CREDIT_VALUE_LENGTH = 160


class AttributionError(RuntimeError):
    """Attribution evidence is malformed, misleading, or unverified."""


@dataclass(frozen=True)
class Registry:
    credit_names: frozenset[str]
    identity_markers: tuple[str, ...]
    allowed_model_sources: frozenset[str]
    allowed_roles: dict[str, frozenset[str]]
    verified_authors: frozenset[tuple[str, str]]
    verified_coauthors: frozenset[tuple[str, str]]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate registry key {key!r}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite registry number {value!r}")


def _identity_pair(value: Any, *, field: str) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise AttributionError(f"{field} must be an object")
    name = value.get("name")
    email = value.get("email")
    if not isinstance(name, str) or not name.strip():
        raise AttributionError(f"{field}.name must be a non-empty string")
    if not isinstance(email, str) or "@" not in email or any(
        character.isspace() for character in email
    ):
        raise AttributionError(f"{field}.email must be a bounded email identity")
    return name.strip(), email.strip().casefold()


def load_registry(path: Path) -> Registry:
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise AttributionError(f"cannot load attribution registry {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise AttributionError("attribution registry schema_version must be 1")

    accountable = value.get("accountable_github_users")
    if not isinstance(accountable, list) or not accountable:
        raise AttributionError("attribution registry needs accountable GitHub users")
    accountable_logins: set[str] = set()
    for index, identity in enumerate(accountable):
        if not isinstance(identity, dict):
            raise AttributionError(f"accountable_github_users[{index}] must be an object")
        login = identity.get("login")
        account_id = identity.get("account_id")
        if (
            not isinstance(login, str)
            or not login.strip()
            or login.casefold() in accountable_logins
            or not isinstance(account_id, int)
            or isinstance(account_id, bool)
            or account_id <= 0
            or identity.get("type") != "User"
        ):
            raise AttributionError("accountable GitHub user identity is invalid")
        accountable_logins.add(login.casefold())

    credits = value.get("agent_credit_identities")
    if not isinstance(credits, list) or not credits:
        raise AttributionError("attribution registry needs agent credit identities")
    credit_names: set[str] = set()
    for index, credit in enumerate(credits):
        if not isinstance(credit, dict):
            raise AttributionError(f"agent_credit_identities[{index}] must be an object")
        name = credit.get("credit_name")
        if not isinstance(name, str) or not name.strip() or name in credit_names:
            raise AttributionError("agent credit names must be non-empty and unique")
        credit_names.add(name.strip())

    markers = value.get("agent_identity_markers")
    if not isinstance(markers, list) or not markers or not all(
        isinstance(item, str) and item.strip() for item in markers
    ):
        raise AttributionError("agent identity markers must be non-empty strings")
    normalized_markers = tuple(sorted({item.strip().casefold() for item in markers}))

    model_sources = value.get("allowed_model_sources")
    if not isinstance(model_sources, list) or not model_sources or not all(
        isinstance(item, str) and ROLE_RE.fullmatch(item) for item in model_sources
    ):
        raise AttributionError("allowed model sources are invalid")

    raw_roles = value.get("allowed_roles")
    if not isinstance(raw_roles, dict) or set(raw_roles) != AGENT_TRAILERS:
        raise AttributionError("allowed_roles must define both Agent-* trailers")
    allowed_roles: dict[str, frozenset[str]] = {}
    for trailer, roles in raw_roles.items():
        if not isinstance(roles, list) or not roles or not all(
            isinstance(role, str) and ROLE_RE.fullmatch(role) for role in roles
        ):
            raise AttributionError(f"allowed roles for {trailer!r} are invalid")
        allowed_roles[trailer] = frozenset(roles)

    verified = value.get("verified_github_agent_identities")
    if not isinstance(verified, list):
        raise AttributionError("verified GitHub agent identities must be a list")
    authors: set[tuple[str, str]] = set()
    coauthors: set[tuple[str, str]] = set()
    for index, identity in enumerate(verified):
        if not isinstance(identity, dict):
            raise AttributionError(
                f"verified_github_agent_identities[{index}] must be an object"
            )
        account_id = identity.get("account_id")
        if (
            not isinstance(identity.get("login"), str)
            or not identity["login"].strip()
            or not isinstance(account_id, int)
            or isinstance(account_id, bool)
            or account_id <= 0
            or identity.get("account_type") != "Bot"
        ):
            raise AttributionError(f"verified GitHub identity {index} is invalid")
        for item_index, item in enumerate(identity.get("author_identities", [])):
            authors.add(
                _identity_pair(
                    item,
                    field=f"verified identity {index} author {item_index}",
                )
            )
        for item_index, item in enumerate(identity.get("coauthor_identities", [])):
            coauthors.add(
                _identity_pair(
                    item,
                    field=f"verified identity {index} coauthor {item_index}",
                )
            )
    return Registry(
        credit_names=frozenset(credit_names),
        identity_markers=normalized_markers,
        allowed_model_sources=frozenset(model_sources),
        allowed_roles=allowed_roles,
        verified_authors=frozenset(authors),
        verified_coauthors=frozenset(coauthors | authors),
    )


def _bounded_credit_value(value: str, *, field: str) -> str:
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > MAX_CREDIT_VALUE_LENGTH
        or any(character in cleaned for character in "<>|\r\n")
        or any(ord(character) < 32 for character in cleaned)
    ):
        raise AttributionError(f"agent credit field {field!r} is invalid")
    return cleaned


def validate_agent_credit(trailer: str, raw_value: str, registry: Registry) -> None:
    trailer_key = trailer.casefold()
    if trailer_key not in AGENT_TRAILERS:
        raise AttributionError(f"unsupported agent trailer {trailer!r}")
    segments = [segment.strip() for segment in raw_value.split("|")]
    if len(segments) not in (5, 6):
        raise AttributionError(
            f"{trailer} must name an agent plus model, model-source, surface, role, "
            "and optional evidence fields"
        )
    credit_name = _bounded_credit_value(segments[0], field="credit_name")
    if credit_name not in registry.credit_names:
        raise AttributionError(f"unknown agent credit identity {credit_name!r}")

    fields: dict[str, str] = {}
    for segment in segments[1:]:
        key, separator, value = segment.partition("=")
        key = key.strip().casefold()
        if not separator or key in fields:
            raise AttributionError("agent credit fields must be unique key=value pairs")
        if key not in REQUIRED_CREDIT_FIELDS | OPTIONAL_CREDIT_FIELDS:
            raise AttributionError(f"unknown agent credit field {key!r}")
        fields[key] = _bounded_credit_value(value, field=key)
    if not REQUIRED_CREDIT_FIELDS.issubset(fields):
        missing = sorted(REQUIRED_CREDIT_FIELDS - fields.keys())
        raise AttributionError(f"agent credit is missing fields: {', '.join(missing)}")
    if fields["model-source"] not in registry.allowed_model_sources:
        raise AttributionError(f"unsupported model-source {fields['model-source']!r}")
    if (fields["model"] == "unverified") != (
        fields["model-source"] == "unverified"
    ):
        raise AttributionError(
            "model=unverified and model-source=unverified must be used together"
        )
    if fields["model-source"] == "runtime-receipt" and "evidence" not in fields:
        raise AttributionError(
            "model-source=runtime-receipt requires a bounded evidence reference"
        )
    role = fields["role"]
    if role not in registry.allowed_roles[trailer_key]:
        raise AttributionError(f"role {role!r} is not allowed for {trailer}")


def _mailbox(raw_value: str) -> tuple[str, str]:
    match = MAILBOX_RE.fullmatch(raw_value.strip())
    if match is None:
        raise AttributionError("Co-authored-by must use Name <linked-email> syntax")
    return match.group(1).strip(), match.group(2).strip().casefold()


def _looks_like_agent(name: str, email: str, registry: Registry) -> bool:
    evidence = f"{name} {email}".casefold()
    return any(marker in evidence for marker in registry.identity_markers)


def _run_git(
    repo: Path,
    *args: str,
    input_text: str | None = None,
) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        text=True,
        input=input_text,
        capture_output=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise AttributionError(f"git {' '.join(args)}: {detail}")
    return result.stdout.strip()


def _resolve_commit(repo: Path, reference: str) -> str:
    return _run_git(repo, "rev-parse", "--verify", f"{reference}^{{commit}}")


def _trailers(repo: Path, message: str) -> list[tuple[str, str]]:
    parsed = _run_git(repo, "interpret-trailers", "--parse", input_text=message)
    trailers: list[tuple[str, str]] = []
    for line in parsed.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            raise AttributionError("git returned an invalid parsed trailer")
        trailers.append((key.strip(), value.strip()))
    return trailers


def _validate_commit(repo: Path, commit: str, registry: Registry) -> list[str]:
    errors: list[str] = []
    author_name = _run_git(repo, "show", "-s", "--format=%an", commit)
    author_email = _run_git(repo, "show", "-s", "--format=%ae", commit).casefold()
    if _looks_like_agent(author_name, author_email, registry) and (
        author_name,
        author_email,
    ) not in registry.verified_authors:
        errors.append(
            "AI-branded Git author is not an exact verified GitHub bot identity"
        )
    message = _run_git(repo, "show", "-s", "--format=%B", commit)
    try:
        trailers = _trailers(repo, message)
    except AttributionError as exc:
        return [str(exc)]
    for key, value in trailers:
        trailer = key.casefold()
        try:
            if trailer in AGENT_TRAILERS:
                validate_agent_credit(key, value, registry)
            elif trailer == "co-authored-by":
                name, email = _mailbox(value)
                if _looks_like_agent(name, email, registry) and (
                    name,
                    email,
                ) not in registry.verified_coauthors:
                    raise AttributionError(
                        "AI-branded Co-authored-by is not an exact verified GitHub identity; "
                        "use Agent-Assisted-By or Agent-Reviewed-By"
                    )
        except AttributionError as exc:
            errors.append(str(exc))
    return errors


def validate_commit_range(
    repo: Path,
    *,
    base_ref: str,
    head_ref: str,
    registry_path: Path | None = None,
) -> list[str]:
    repo = Path(_run_git(repo, "rev-parse", "--show-toplevel"))
    registry = load_registry(
        registry_path or repo / ".github" / "agent-identities.json"
    )
    base = _resolve_commit(repo, base_ref)
    head = _resolve_commit(repo, head_ref)
    merge_base = _run_git(repo, "merge-base", base, head)
    commits = _run_git(repo, "rev-list", "--reverse", f"{merge_base}..{head}")
    errors: list[str] = []
    for commit in filter(None, commits.splitlines()):
        for error in _validate_commit(repo, commit, registry):
            errors.append(f"{commit[:12]}: {error}")
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--head", default="HEAD", dest="head_ref")
    parser.add_argument("--registry", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        repo = Path(_run_git(Path.cwd(), "rev-parse", "--show-toplevel"))
        errors = validate_commit_range(
            repo,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
            registry_path=args.registry,
        )
    except AttributionError as exc:
        print(f"ATTRIBUTION CHECK FAILED: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("ATTRIBUTION CHECK FAILED:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Attribution check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
