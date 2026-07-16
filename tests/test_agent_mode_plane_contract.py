"""Hermetic locks for Grok IDE agent modes, planes, and discover_self models.

Complements dual-plane model-discipline tests: this module locks the public
``agent`` / ``public_agent`` surface (modes + plane + fallback_policy), mode
dials, and the discover_self include_models plane map — Grok IDE superpowers.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Literal, get_args, get_origin, get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.http_server import MODE_DIAL_PORTS, public_agent
from src.tools.chats import agent as stdio_agent

ROOT = Path(__file__).resolve().parents[1]

AGENT_MODES = frozenset({"auto", "fast", "reasoning", "thinking", "research"})
AGENT_PLANES = frozenset({"auto", "cli", "api"})
FALLBACK_POLICIES = frozenset({"same_plane", "cross_plane"})
FOREIGN_PLANE_NAMES = frozenset({"openai", "anthropic", "gemini", "vertex", "sol"})


def _literal_values(annotation) -> set[str]:
    """Extract string Literal members; unwrap Optional[Literal[...]]."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is None:
        return set()
    # Optional[X] / Union[X, None]
    if type(None) in args:
        non_none = tuple(a for a in args if a is not type(None))
        if len(non_none) == 1:
            annotation = non_none[0]
            origin = get_origin(annotation)
            args = get_args(annotation) if origin is not None else ()
    if origin is Literal:
        return {a for a in args if isinstance(a, str)}
    out: set[str] = set()
    for a in args:
        if get_origin(a) is Literal:
            out.update(x for x in get_args(a) if isinstance(x, str))
    return out


def test_stdio_agent_mode_plane_fallback_literals() -> None:
    hints = get_type_hints(stdio_agent)
    assert _literal_values(hints["mode"]) == AGENT_MODES
    assert _literal_values(hints["plane"]) == AGENT_PLANES
    assert _literal_values(hints["fallback_policy"]) == FALLBACK_POLICIES
    assert FOREIGN_PLANE_NAMES.isdisjoint(_literal_values(hints["plane"]))


def test_public_agent_mode_plane_fallback_match_stdio() -> None:
    pub = get_type_hints(public_agent)
    std = get_type_hints(stdio_agent)
    assert _literal_values(pub["mode"]) == _literal_values(std["mode"]) == AGENT_MODES
    assert _literal_values(pub["plane"]) == _literal_values(std["plane"]) == AGENT_PLANES
    assert (
        _literal_values(pub["fallback_policy"])
        == _literal_values(std["fallback_policy"])
        == FALLBACK_POLICIES
    )


def test_mode_dial_ports_map_only_to_agent_modes() -> None:
    assert MODE_DIAL_PORTS
    for port, mode in MODE_DIAL_PORTS.items():
        assert isinstance(port, int)
        assert mode in AGENT_MODES, (port, mode)
    assert set(MODE_DIAL_PORTS.values()) == AGENT_MODES


def test_using_unigrok_skill_documents_modes_and_planes() -> None:
    skill = (
        ROOT / ".agents" / "skills" / "using-unigrok" / "SKILL.md"
    ).read_text(encoding="utf-8")
    public = (ROOT / "skills" / "using-unigrok" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert skill == public
    for mode in sorted(AGENT_MODES):
        assert mode in skill
    for token in ("cli", "api", "same_plane", "cross_plane", "cli_first"):
        assert token in skill
    # Live product law: thinking/research are API-native under same_plane pins.
    assert "API-only" in skill
    assert "same_plane_capability_incompatible" in skill or "cli-incompatible" in skill


def test_agent_tool_and_faq_document_api_only_mode_plane_law() -> None:
    agent_tool = (ROOT / "docs" / "okf" / "agent-tool.md").read_text(encoding="utf-8")
    faq = (ROOT / "docs" / "okf" / "faq.md").read_text(encoding="utf-8")
    assert "API-native" in agent_tool or "API-only" in agent_tool
    assert "same_plane_capability_incompatible" in agent_tool
    assert "{#api-only-modes}" in faq
    assert "cli-incompatible" in faq


def test_agent_docstrings_name_all_modes() -> None:
    doc = inspect.getdoc(stdio_agent) or ""
    for mode in AGENT_MODES:
        assert mode in doc
    assert "cli" in doc and "api" in doc
    assert "same_plane" in doc and "cross_plane" in doc


class _NullCtx:
    def __init__(self) -> None:
        self.logger = MagicMock()
        self.elapsed = 0.0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def format_output(self, text: str) -> str:
        return text


@pytest.mark.asyncio
async def test_discover_self_include_models_exposes_cli_and_api_planes_only() -> None:
    from src.tools import system as system_mod

    catalog = {
        "xai_api": [{"id": "grok-build-0.1"}],
        "grok_cli": [{"id": "grok-4.5"}],
        "local_profiles": [],
        "default_cli_model": "grok-4.5",
        "warnings": [],
        "sources": {"xai_api": "t", "grok_cli": "t", "local_profiles": "t"},
        "availability": {"xai_api": True, "grok_cli": True},
    }
    cred = {
        "policy": "cli_first",
        "preferred_plane": "CLI",
        "effective_plane": "CLI",
        "api": {"available": True, "state": "configured"},
        "cli": {"available": True, "state": "ready"},
    }
    bootstrap = {
        "status": "OK",
        "can_chat": True,
        "can_spend_api": True,
        "can_mutate_workspace": False,
        "can_use_swarm": False,
        "surfaces": {
            "canonical_mcp": "http://localhost:4765/mcp",
            "ui": "http://localhost:4765/ui/",
        },
        "next_actions": [],
    }
    okf = {"root": "index.md", "files": ["index.md"]}

    with (
        patch.object(system_mod, "credential_plane_contract", return_value=cred),
        patch.object(
            system_mod, "build_model_catalog", new=AsyncMock(return_value=catalog)
        ),
        patch.object(system_mod, "load_okf_manifest", return_value=okf),
        patch.object(
            system_mod,
            "_build_discover_request_context",
            return_value={
                "surface": "stable_core",
                "mode_dials_enabled": False,
                "client_id_normalized": "test",
            },
        ),
        patch.object(
            system_mod, "_build_discover_bootstrap", return_value=bootstrap
        ),
        patch.object(system_mod.PathResolver, "get_workspace_root", return_value=None),
        patch.object(system_mod.PathResolver, "contributor_mode", return_value=False),
        patch.object(system_mod, "GrokInvocationContext", return_value=_NullCtx()),
        patch.object(
            system_mod,
            "run_blocking",
            new=AsyncMock(
                return_value={
                    "state": "ready",
                    "ready": True,
                    "binary": True,
                    "auth": "oauth_verified",
                }
            ),
        ),
    ):
        result = await system_mod.grok_mcp_discover_self(include_models=True)

    data = getattr(result, "data", None)
    assert isinstance(data, dict)
    mc = data.get("model_catalog")
    assert isinstance(mc, dict)
    planes = mc["planes"]
    assert set(planes.keys()) == {"CLI", "API"}
    assert FOREIGN_PLANE_NAMES.isdisjoint(k.lower() for k in planes)
    assert "shared_model_ids" in mc
    assert mc["routing"]["policy"] == "cli_first"
    # Mode dials on discover_self still only name agent modes
    dials = data.get("mode_dials", {}).get("ports", {})
    for mode in dials.values():
        assert mode in AGENT_MODES
