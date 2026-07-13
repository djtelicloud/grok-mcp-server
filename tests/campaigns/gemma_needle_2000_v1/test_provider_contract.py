import json
import stat
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from evals.campaigns.gemma_needle_2000_v1.provider_adapters import (
    ProviderAdapter,
    RunMode,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
REQUEST = "Return the provider wiring probe as JSON."
SCHEMA_VERSION = "provider-probe-v1"
TEMPLATE_DIGEST = "sha256:provider-probe-template"
SAFE_SETTINGS = {"temperature": 0, "max_output_tokens": 48}


class ProbeArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probe: Literal["provider-contract-v1"]
    nonce: str


def _adapter(tmp_path: Path, **overrides) -> ProviderAdapter:
    params = {
        "provider": "provider-a",
        "model": "model-a",
        "plane": "api",
        "role": "seed-author",
        "mode": RunMode.MOCK,
        "cache_root": tmp_path / "provider-cache",
        "credential_binding_id": "binding-a",
    }
    params.update(overrides)
    return ProviderAdapter(**params)


def _execute(
    adapter: ProviderAdapter,
    *,
    settings: dict | None = None,
    response_schema: type[BaseModel] | None = ProbeArtifact,
) -> dict:
    return adapter.execute(
        request=REQUEST,
        schema_version=SCHEMA_VERSION,
        template_digest=TEMPLATE_DIGEST,
        settings=SAFE_SETTINGS if settings is None else settings,
        response_schema=response_schema,
    )


def _content(response: dict) -> dict:
    return json.loads(response["content"])


def _transport_receipt(
    *,
    provider: str = "provider-a",
    model: str = "model-a",
    plane: str = "api",
) -> dict:
    return {
        "configured_model": model,
        "provider": provider,
        "resolved_model": model,
        "resolved_plane": plane,
    }


def test_json_artifact_parser_accepts_only_one_pure_json_object(tmp_path: Path):
    adapter = _adapter(tmp_path)
    expected = {"probe": "provider-contract-v1", "nonce": "n-1"}

    assert adapter.extract_json_artifact(json.dumps(expected)) == expected
    assert adapter.extract_json_artifact(f"  {json.dumps(expected)}\n") == expected

    rejected = [
        f"I will do that.\n{json.dumps(expected)}",
        f"```json\n{json.dumps(expected)}\n```",
        f"{json.dumps(expected)}\nFinished.",
        json.dumps([expected]),
        "{not-json}",
        "",
    ]
    for content in rejected:
        assert adapter.extract_json_artifact(content) is None


def test_live_refuses_to_run_without_an_injected_transport(tmp_path: Path):
    adapter = _adapter(tmp_path, mode=RunMode.LIVE)

    with pytest.raises((RuntimeError, ValueError), match="(?i)transport"):
        _execute(adapter)


def test_live_refuses_to_call_transport_without_a_response_schema(tmp_path: Path):
    calls = 0

    def transport(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "content": '{"probe":"provider-contract-v1","nonce":"live"}',
            "transport_receipt": _transport_receipt(),
        }

    adapter = _adapter(tmp_path, mode=RunMode.LIVE, transport=transport)

    with pytest.raises((RuntimeError, ValueError), match="(?i)schema"):
        _execute(adapter, response_schema=None)
    assert calls == 0


def test_live_rejects_prose_wrapped_or_wrong_schema_responses_before_caching(
    tmp_path: Path,
):
    responses = iter(
        [
            {
                "content": (
                    "Here it is:\n"
                    '{"probe":"provider-contract-v1","nonce":"wrapped"}'
                ),
                "transport_receipt": _transport_receipt(),
            },
            {
                "content": (
                    '{"probe":"provider-contract-v1","nonce":"extra",'
                    '"unexpected":true}'
                ),
                "transport_receipt": _transport_receipt(),
            },
        ]
    )

    def transport(*args, **kwargs):
        return next(responses)

    adapter = _adapter(tmp_path, mode=RunMode.LIVE, transport=transport)

    with pytest.raises((RuntimeError, ValueError), match="(?i)(json|schema|response)"):
        _execute(adapter)
    with pytest.raises((RuntimeError, ValueError), match="(?i)(json|schema|response)"):
        _execute(adapter)

    assert not list((tmp_path / "provider-cache" / "live").rglob("*.json"))


def test_mock_live_and_replay_namespaces_cannot_cross_contaminate(tmp_path: Path):
    cache_root = tmp_path / "provider-cache"

    mock = _adapter(tmp_path, mode=RunMode.MOCK, cache_root=cache_root)
    _execute(mock, response_schema=None)

    replay_before_live = _adapter(tmp_path, mode=RunMode.REPLAY, cache_root=cache_root)
    with pytest.raises((RuntimeError, ValueError), match="(?i)(cache|replay|miss)"):
        _execute(replay_before_live)

    calls = 0

    def transport(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "content": json.dumps(
                {
                    "probe": "provider-contract-v1",
                    "nonce": f"live-{calls}",
                }
            ),
            "transport_receipt": _transport_receipt(),
        }

    live = _adapter(
        tmp_path,
        mode=RunMode.LIVE,
        cache_root=cache_root,
        transport=transport,
    )
    assert _content(_execute(live))["nonce"] == "live-1"

    # LIVE must execute its transport even when a matching LIVE cache exists.
    assert _content(_execute(live))["nonce"] == "live-2"
    assert calls == 2

    replay = _adapter(tmp_path, mode=RunMode.REPLAY, cache_root=cache_root)
    replayed = _execute(replay)
    assert _content(replayed)["nonce"] == "live-2"
    assert replayed.get("_cache_hit") is True
    assert calls == 2

    assert list((cache_root / "mock").rglob("*.json"))
    assert list((cache_root / "live").rglob("*.json"))
    assert not (cache_root / "replay").exists()


def test_cache_is_partitioned_by_binding_provider_model_role_and_plane(tmp_path: Path):
    cache_root = tmp_path / "provider-cache"
    dimensions = [
        {},
        {"credential_binding_id": "binding-b"},
        {"provider": "provider-b"},
        {"model": "model-b"},
        {"role": "critic"},
        {"plane": "cli"},
    ]

    for changed_dimension in dimensions:
        adapter = _adapter(
            tmp_path,
            cache_root=cache_root,
            mode=RunMode.MOCK,
            **changed_dimension,
        )
        _execute(adapter, response_schema=None)

    assert len(list((cache_root / "mock").rglob("*.json"))) == len(dimensions)


def test_cache_directories_and_files_are_owner_only(tmp_path: Path):
    cache_root = tmp_path / "provider-cache"
    adapter = _adapter(tmp_path, cache_root=cache_root, mode=RunMode.MOCK)
    _execute(adapter, response_schema=None)

    directories = [cache_root, *(p for p in cache_root.rglob("*") if p.is_dir())]
    files = [p for p in cache_root.rglob("*") if p.is_file()]

    assert directories
    assert files
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in directories)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)


@pytest.mark.parametrize(
    "settings",
    [
        {"api_key": "redacted-test-value"},
        {"authorization": "Bearer redacted-test-value"},
        {"provider": {"access_token": "redacted-test-value"}},
        {"provider": {"client_secret": "redacted-test-value"}},
        {"note": "xai-this-is-a-secret-shaped-settings-value"},
    ],
)
def test_secret_like_settings_are_rejected_before_cache_write(
    tmp_path: Path, settings: dict
):
    cache_root = tmp_path / "provider-cache"
    adapter = _adapter(tmp_path, cache_root=cache_root, mode=RunMode.MOCK)

    with pytest.raises((RuntimeError, ValueError), match="(?i)(secret|credential|setting)"):
        _execute(adapter, settings=settings, response_schema=None)

    assert not list(cache_root.rglob("*.json"))


def test_nonsecret_token_budget_setting_remains_allowed(tmp_path: Path):
    adapter = _adapter(tmp_path, mode=RunMode.MOCK)

    result = _execute(
        adapter,
        settings={"temperature": 0, "max_output_tokens": 48},
        response_schema=None,
    )

    assert isinstance(result, dict)


def test_secret_like_provider_output_is_rejected_before_cache_write(tmp_path: Path):
    cache_root = tmp_path / "provider-cache"

    def transport(*args, **kwargs):
        return {
            "content": json.dumps(
                {
                    "probe": "provider-contract-v1",
                    "nonce": "xai-this-is-a-fake-but-secret-shaped-value",
                }
            ),
            "transport_receipt": _transport_receipt(),
        }

    adapter = _adapter(
        tmp_path,
        cache_root=cache_root,
        mode=RunMode.LIVE,
        transport=transport,
    )

    with pytest.raises(ValueError, match="(?i)secret"):
        _execute(adapter)

    assert not list(cache_root.rglob("*.json"))


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        _transport_receipt(provider="provider-b"),
        _transport_receipt(model="model-b"),
        _transport_receipt(plane="cli"),
    ],
)
def test_live_requires_matching_provider_provenance_before_caching(
    tmp_path: Path,
    receipt: dict | None,
):
    cache_root = tmp_path / "provider-cache"

    def transport(*args, **kwargs):
        response = {
            "content": '{"probe":"provider-contract-v1","nonce":"live"}'
        }
        if receipt is not None:
            response["transport_receipt"] = receipt
        return response

    adapter = _adapter(
        tmp_path,
        cache_root=cache_root,
        mode=RunMode.LIVE,
        transport=transport,
    )

    with pytest.raises(ValueError, match="(?i)(receipt|provider|model|plane)"):
        _execute(adapter)

    assert not list(cache_root.rglob("*.json"))


def test_replay_rejects_insecure_or_integrity_mismatched_cache(tmp_path: Path):
    cache_root = tmp_path / "provider-cache"

    def transport(*args, **kwargs):
        return {
            "content": '{"probe":"provider-contract-v1","nonce":"live"}',
            "transport_receipt": _transport_receipt(),
        }

    live = _adapter(
        tmp_path,
        cache_root=cache_root,
        mode=RunMode.LIVE,
        transport=transport,
    )
    _execute(live)
    cache_file = next((cache_root / "live").rglob("*.json"))
    cache_file.chmod(0o644)

    replay = _adapter(tmp_path, cache_root=cache_root, mode=RunMode.REPLAY)
    with pytest.raises(ValueError, match="(?i)(cache|replay)"):
        _execute(replay)

    cache_file.chmod(0o600)
    envelope = json.loads(cache_file.read_text(encoding="utf-8"))
    envelope["response"]["content"] = (
        '{"probe":"provider-contract-v1","nonce":"tampered"}'
    )
    cache_file.write_text(json.dumps(envelope), encoding="utf-8")
    cache_file.chmod(0o600)
    with pytest.raises(ValueError, match="(?i)(cache|replay)"):
        _execute(replay)


def test_cache_root_inside_repository_is_rejected_without_creating_it():
    cache_root = REPO_ROOT / ".provider-cache-contract-test"
    assert not cache_root.exists()

    with pytest.raises((RuntimeError, ValueError), match="(?i)(cache|repository|workspace)"):
        ProviderAdapter(
            provider="provider-a",
            model="model-a",
            plane="api",
            role="seed-author",
            mode=RunMode.MOCK,
            cache_root=cache_root,
            credential_binding_id="binding-a",
        )

    assert not cache_root.exists()


def test_existing_insecure_cache_root_is_rejected_without_chmod(tmp_path: Path):
    cache_root = tmp_path / "shared-cache"
    cache_root.mkdir(mode=0o755)
    cache_root.chmod(0o755)

    with pytest.raises(ValueError, match="(?i)(owner|0700)"):
        _adapter(tmp_path, cache_root=cache_root)

    assert stat.S_IMODE(cache_root.stat().st_mode) == 0o755


def test_symlink_cache_root_is_rejected(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "cache-link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="(?i)symbolic"):
        _adapter(tmp_path, cache_root=link)


def test_secret_shaped_request_or_binding_id_is_rejected(tmp_path: Path):
    adapter = _adapter(tmp_path)
    with pytest.raises(ValueError, match="(?i)secret"):
        adapter.execute(
            request="xai-this-is-a-secret-shaped-request-value",
            schema_version=SCHEMA_VERSION,
            template_digest=TEMPLATE_DIGEST,
            settings=SAFE_SETTINGS,
        )

    with pytest.raises(ValueError, match="(?i)(safe|opaque|binding)"):
        _adapter(
            tmp_path,
            credential_binding_id="xai-secret-shaped-binding-value",
        )


def test_provider_specific_transport_receipt_contracts(tmp_path: Path):
    content = '{"probe":"provider-contract-v1","nonce":"live"}'
    vertex = ProviderAdapter(
        provider="vertex",
        model="gemini-2.5-flash",
        plane="api",
        role="mutation-smoke",
        mode=RunMode.LIVE,
        cache_root=tmp_path / "vertex-cache",
        credential_binding_id="google-adc-default",
    )
    vertex_receipt = {
        "auth_kind": "google_adc",
        "configured_model": "gemini-2.5-flash",
        "provider": "vertex",
        "resolved_model": "gemini-2.5-flash",
        "resolved_plane": "api",
        "total_attempt_limit": 1,
    }
    assert vertex.validate_response(
        {"content": content, "transport_receipt": vertex_receipt},
        ProbeArtifact,
        require_transport_receipt=True,
    )
    for field, wrong in (
        ("auth_kind", "api_key"),
        ("total_attempt_limit", 2),
        ("resolved_plane", "cli"),
    ):
        invalid = {**vertex_receipt, field: wrong}
        assert not vertex.validate_response(
            {"content": content, "transport_receipt": invalid},
            ProbeArtifact,
            require_transport_receipt=True,
        )

    unigrok = ProviderAdapter(
        provider="unigrok",
        model="grok-4.5",
        plane="cli",
        role="critic-smoke",
        mode=RunMode.LIVE,
        cache_root=tmp_path / "unigrok-cache",
        credential_binding_id="unigrok-mcp-local",
    )
    unigrok_receipt = {
        "auth_kind": "server_managed",
        "configured_model": "grok-4.5",
        "fallback_policy": "same_plane",
        "finish_reason": "final_answer",
        "provider": "unigrok",
        "resolved_model": "grok-4.5",
        "resolved_plane": "CLI",
    }
    assert unigrok.validate_response(
        {"content": content, "transport_receipt": unigrok_receipt},
        ProbeArtifact,
        require_transport_receipt=True,
    )
    for field, wrong in (
        ("auth_kind", "copied_oauth"),
        ("fallback_policy", "cross_plane"),
        ("finish_reason", "fallback"),
        ("resolved_model", "other-model"),
    ):
        invalid = {**unigrok_receipt, field: wrong}
        assert not unigrok.validate_response(
            {"content": content, "transport_receipt": invalid},
            ProbeArtifact,
            require_transport_receipt=True,
        )


def test_cache_rejects_a_sibling_git_workspace(tmp_path: Path):
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir(mode=0o700)
    (other_repo / ".git").mkdir(mode=0o700)
    cache_root = other_repo / "campaign-cache"

    with pytest.raises(ValueError, match="(?i)(cache|repository)"):
        _adapter(tmp_path, cache_root=cache_root)

    assert not cache_root.exists()
