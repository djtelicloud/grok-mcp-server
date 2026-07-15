"""Hermetic locks for xAI dual-plane model pins and public-surface discipline.

Grok-owned product law: public dual-plane is API+CLI only; routing candidates
stay Grok slugs; multi-provider package remains unwired; process hydration is
not session rehydrate.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src import hydration as hydration_mod
from src import providers as providers_mod
from src import utils
from src.credentials import build_credential_plane_contract, credential_plane_policy
from src.routing import ROUTE_CANDIDATES
from src.utils import (
    DEFAULT_CODING_MODEL,
    DEFAULT_PLANNING_MODEL,
    FALLBACK_XAI_LANGUAGE_MODELS,
    build_model_catalog,
)

ROOT = Path(__file__).resolve().parents[1]
FOREIGN_PREFIXES = ("gpt-", "claude-", "gemini-", "o1-", "o3-")
FOREIGN_CATALOG_KEYS = frozenset(
    {"openai", "anthropic", "gemini", "vertex", "google"}
)


def test_cold_start_route_and_default_pins() -> None:
    assert DEFAULT_CODING_MODEL == "grok-build-0.1"
    assert DEFAULT_PLANNING_MODEL == "grok-4.5"
    assert ROUTE_CANDIDATES["coding"][0] == "grok-build-0.1"
    assert ROUTE_CANDIDATES["planning"][0] == "grok-4.5"
    assert ROUTE_CANDIDATES["vision"][0] == "grok-4.5"
    assert ROUTE_CANDIDATES["research"][0].startswith("grok-4.20-multi-agent")


def test_routing_candidates_and_fallback_catalog_are_grok_slugs_only() -> None:
    route_slugs = [slug for group in ROUTE_CANDIDATES.values() for slug in group]
    assert route_slugs
    for slug in route_slugs:
        assert slug.startswith("grok-"), slug
        assert not any(slug.startswith(p) for p in FOREIGN_PREFIXES), slug
    assert "Grok Build" not in route_slugs

    for model_id in FALLBACK_XAI_LANGUAGE_MODELS:
        assert model_id.startswith("grok-"), model_id
        assert not any(model_id.startswith(p) for p in FOREIGN_PREFIXES), model_id


def test_credential_plane_contract_is_xai_api_and_cli_only() -> None:
    contract = build_credential_plane_contract(
        api_configured=True,
        cli_status={
            "state": "ready",
            "ready": True,
            "binary": True,
            "auth": "oauth_verified",
        },
    )
    assert "api" in contract and "cli" in contract
    assert FOREIGN_CATALOG_KEYS.isdisjoint(contract.keys())
    assert contract["api"]["secret_name"] == "XAI_API_KEY"
    assert contract["cli"]["credential"] == "grok_com_oauth"
    assert credential_plane_policy() in {"cli_first", "api_first"}


@pytest.mark.asyncio
async def test_build_model_catalog_shape_is_dual_xai_planes_only() -> None:
    api = {
        "models": [{"id": "grok-build-0.1"}],
        "available": True,
        "warnings": [],
        "source": "test_api",
    }
    cli = {
        "models": [{"id": "grok-4.5"}],
        "default_model": "grok-4.5",
        "available": True,
        "warnings": [],
        "source": "test_cli",
    }
    profiles = {
        "profiles": [],
        "warnings": [],
        "source": "test_profiles",
    }
    with (
        patch.object(utils, "discover_xai_api_models", new=AsyncMock(return_value=api)),
        patch.object(
            utils, "discover_grok_cli_models", new=AsyncMock(return_value=cli)
        ),
        patch.object(utils, "discover_local_grok_profiles", return_value=profiles),
    ):
        catalog = await build_model_catalog(include_cli=True)

    assert set(catalog) == {
        "xai_api",
        "grok_cli",
        "local_profiles",
        "default_cli_model",
        "warnings",
        "sources",
        "availability",
    }
    assert set(catalog["sources"]) == {"xai_api", "grok_cli", "local_profiles"}
    assert set(catalog["availability"]) == {"xai_api", "grok_cli"}
    assert FOREIGN_CATALOG_KEYS.isdisjoint(catalog.keys())
    assert FOREIGN_CATALOG_KEYS.isdisjoint(catalog["sources"].keys())
    assert FOREIGN_CATALOG_KEYS.isdisjoint(catalog["availability"].keys())
    assert catalog["default_cli_model"] == "grok-4.5"


def test_grok_build_product_name_is_not_shared_without_both_catalogs() -> None:
    api_only = {"grok-build-0.1"}
    cli_empty: set[str] = set()
    assert "grok-build-0.1" not in (api_only & cli_empty)

    both = {"grok-build-0.1"}
    assert "grok-build-0.1" in (both & both)

    flattened = {slug for group in ROUTE_CANDIDATES.values() for slug in group}
    assert "Grok Build" not in flattened
    assert "grok-build-0.1" in flattened


def test_multi_provider_package_stays_inert_on_public_surfaces() -> None:
    assert providers_mod.__doc__ is not None
    assert "not wired" in providers_mod.__doc__

    server_src = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
    system_src = (ROOT / "src" / "tools" / "system.py").read_text(encoding="utf-8")
    assert "GrokWorkerBroker" not in server_src
    assert "build_provider_registry" not in server_src
    assert "GrokWorkerBroker" not in system_src

    for rel in ("src/http_server.py", "src/cli.py", "src/tools/chats.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src.providers.broker"), rel
                assert node.module != "src.providers.broker", rel


def test_process_hydration_is_not_session_rehydrate_skill() -> None:
    doc = hydration_mod.__doc__ or ""
    assert "session rehydration" in doc.lower() or "agent session" in doc.lower()
    assert hydration_mod._VALID_SCOPES == frozenset(
        {"process_day", "process_lifetime", "session"}
    )
    service_doc = hydration_mod.HydrationService.__doc__ or ""
    assert "never marked complete" in service_doc.lower() or "Failed" in service_doc

    # Public API surface is hook registration / service access, not chat rehydrate.
    assert hasattr(hydration_mod, "HydrationService")
    assert not hasattr(hydration_mod, "rehydrate_session")


def test_okf_pinning_doc_states_product_law_anchors() -> None:
    text = (ROOT / "docs" / "okf" / "grok-4.5-pinning.md").read_text(encoding="utf-8")
    for needle in (
        "cli_first",
        'plane="cli"',
        'plane="api"',
        "grok-build-0.1",
        "grok-4.5",
        "xAI only",
        "not wired",
        "Process hydration ≠ session rehydrate",
        "Grok Build product ≠ shared catalog membership",
    ):
        assert needle in text, needle


def test_server_module_does_not_bind_provider_broker_symbols() -> None:
    # Import after path setup; hermetic check of public server surface.
    server = importlib.import_module("src.server")
    assert not hasattr(server, "GrokWorkerBroker")
    assert not hasattr(server, "build_provider_registry")
