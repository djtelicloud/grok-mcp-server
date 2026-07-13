import json
import stat
from pathlib import Path

import pytest

from evals.campaigns.gemma_needle_2000_v1 import provider_smoke
from evals.campaigns.gemma_needle_2000_v1.provider_adapters import RunMode
from evals.campaigns.gemma_needle_2000_v1.provider_transports import (
    PROVIDER_PROBE_JSON_SCHEMA,
    UniGrokMCPTransport,
    VertexADCTransport,
)


def _profile_payload() -> dict:
    return {
        "version": 1,
        "campaign_id": "gemma-needle-2000-v1",
        "max_live_calls": 2,
        "orchestrator_retry_limit": 0,
        "vertex_max_output_tokens": 64,
        "bindings": [
            {
                "provider": "vertex",
                "credential_binding_id": "google-adc-default",
                "auth_kind": "google_adc",
                "project": "example-project",
                "location": "global",
                "model": "gemini-2.5-flash",
                "role": "mutation-adjudication-smoke",
            },
            {
                "provider": "unigrok",
                "credential_binding_id": "unigrok-mcp-local",
                "auth_kind": "server_managed",
                "endpoint": "http://127.0.0.1:4765/mcp",
                "model": "grok-4.5",
                "plane": "cli",
                "fallback_policy": "same_plane",
                "role": "seed-critic-smoke",
            },
        ],
    }


def _private_profile(tmp_path: Path) -> Path:
    tmp_path.chmod(0o700)
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(_profile_payload()), encoding="utf-8")
    path.chmod(0o600)
    return path


def _provider_receipt(binding) -> dict:
    receipt = {
        "configured_model": binding.model,
        "provider": binding.provider,
        "resolved_model": binding.model,
        "resolved_plane": getattr(binding, "plane", "api"),
    }
    if binding.provider == "vertex":
        receipt.update(
            {
                "auth_kind": "google_adc",
                "total_attempt_limit": 1,
            }
        )
    else:
        receipt.update(
            {
                "auth_kind": "server_managed",
                "fallback_policy": "same_plane",
                "finish_reason": "final_answer",
            }
        )
    return receipt


def test_profile_requires_owner_private_permissions(tmp_path: Path):
    path = _private_profile(tmp_path)
    assert provider_smoke.load_profile(path).max_live_calls == 2

    path.chmod(0o644)
    with pytest.raises(ValueError, match="(?i)(owner|0600)"):
        provider_smoke.load_profile(path)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:4765/mcp",
        "http://example.com/mcp",
        "http://user:password@127.0.0.1:4765/mcp",
        "http://127.0.0.1:4765/not-mcp",
        "http://127.0.0.1:9999/mcp",
        "http://localhost:4765/mcp",
    ],
)
def test_profile_rejects_nonloopback_or_credentialed_mcp_urls(
    tmp_path: Path,
    endpoint: str,
):
    payload = _profile_payload()
    payload["bindings"][1]["endpoint"] = endpoint
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ValueError, match="(?i)validation"):
        provider_smoke.load_profile(path)


def test_live_then_replay_uses_two_calls_then_zero_calls(
    monkeypatch,
    tmp_path: Path,
):
    profile = provider_smoke.load_profile(_private_profile(tmp_path))
    cache_root = tmp_path / "cache"
    calls = []

    def fake_transport(binding):
        def call(request, settings):
            calls.append(binding.provider)
            nonce = request.split('"nonce":"', 1)[1].split('"', 1)[0]
            return {
                "content": json.dumps(
                    {"probe": "provider-contract-v1", "nonce": nonce}
                ),
                "transport_receipt": _provider_receipt(binding),
            }

        return call

    monkeypatch.setattr(provider_smoke, "_transport", fake_transport)
    monkeypatch.setattr(
        "evals.campaigns.gemma_needle_2000_v1.provider_adapters.default_cache_root",
        lambda: cache_root,
    )
    receipts_root = tmp_path / "receipts"

    live, live_passed = provider_smoke.run_smoke(
        profile=profile,
        mode=RunMode.LIVE,
        nonce="stage0-5-test-nonce",
        receipts_root=receipts_root,
    )
    assert live_passed is True
    assert calls == ["vertex", "unigrok"]

    def transport_must_not_be_built(binding):
        raise AssertionError("replay constructed a provider transport")

    monkeypatch.setattr(provider_smoke, "_transport", transport_must_not_be_built)
    replay, replay_passed = provider_smoke.run_smoke(
        profile=profile,
        mode=RunMode.REPLAY,
        nonce="stage0-5-test-nonce",
        receipts_root=receipts_root,
    )
    assert replay_passed is True
    assert calls == ["vertex", "unigrok"]
    assert live["provider_statuses"] == {"vertex": "passed", "unigrok": "passed"}
    assert replay["provider_statuses"] == live["provider_statuses"]

    live_receipt = json.loads(Path(live["receipt_path"]).read_text(encoding="utf-8"))
    assert live_receipt["evidence_class"] == "live_transport_contract_passed"
    replay_receipt = json.loads(Path(replay["receipt_path"]).read_text(encoding="utf-8"))
    assert replay_receipt["transport_responses_observed_current_run"] == 0
    assert replay_receipt["transport_invocation_attempts_current_run"] == 0
    assert replay_receipt["evidence_class"] == (
        "cache_integrity_checked_origin_unverified"
    )
    assert all(
        provider["transport_invoked_current_run"] is False
        and provider["artifact_source"] == "live_cache"
        and provider["evidence_class"] == "cache_integrity_checked_origin_unverified"
        and provider["source_transport_receipt_digest"].startswith("sha256:")
        for provider in replay_receipt["providers"]
    )

    for path in cache_root.rglob("*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o700 if path.is_dir() else 0o600)
    for path in receipts_root.rglob("*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o700 if path.is_dir() else 0o600)


def test_unigrok_transport_uses_the_public_agent_prompt_contract():
    transport = UniGrokMCPTransport(
        endpoint="http://127.0.0.1:4765/mcp",
        model="grok-4.5",
    )

    arguments = transport.agent_arguments("probe")

    assert arguments["prompt"] == "probe"
    assert "task" not in arguments
    assert arguments["plane"] == "cli"
    assert arguments["fallback_policy"] == "same_plane"
    http_options = transport.http_client_options({"X-Client-ID": "test"})
    assert http_options["trust_env"] is False

    with pytest.raises(ValueError, match="(?i)canonical"):
        UniGrokMCPTransport(
            endpoint="http://localhost:4765/mcp",
            model="grok-4.5",
        )


def test_vertex_transport_has_one_total_sdk_attempt():
    transport = VertexADCTransport(
        project="example-project",
        location="global",
        model="gemini-2.5-flash",
    )

    assert transport.total_attempt_limit == 1
    assert transport.thinking_budget == 0
    assert PROVIDER_PROBE_JSON_SCHEMA["additionalProperties"] is False
    assert set(PROVIDER_PROBE_JSON_SCHEMA["required"]) == {"probe", "nonce"}

    class FakeRetryOptions:
        def __init__(self, *, attempts):
            self.attempts = attempts

    class FakeHttpOptions:
        def __init__(self, *, api_version, retry_options):
            self.api_version = api_version
            self.retry_options = retry_options

    class FakeTypes:
        HttpRetryOptions = FakeRetryOptions
        HttpOptions = FakeHttpOptions

    options = transport.build_http_options(FakeTypes)
    assert options.api_version == "v1"
    assert options.retry_options.attempts == 1


def test_profile_symlink_and_insecure_parent_are_rejected(tmp_path: Path):
    path = _private_profile(tmp_path)
    link = tmp_path / "profile-link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="(?i)symbolic"):
        provider_smoke.load_profile(link)

    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="(?i)(parent|0700)"):
        provider_smoke.load_profile(path)
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755


def test_profile_rejects_secret_shaped_binding_identifiers(tmp_path: Path):
    tmp_path.chmod(0o700)
    payload = _profile_payload()
    payload["bindings"][0]["credential_binding_id"] = (
        "xai-secret-shaped-binding-identifier"
    )
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ValueError, match="(?i)secret"):
        provider_smoke.load_profile(path)


def test_receipt_root_inside_repository_is_rejected(tmp_path: Path):
    profile = provider_smoke.load_profile(_private_profile(tmp_path))
    repo_receipts = Path(__file__).resolve().parents[3] / ".provider-receipts-test"
    assert not repo_receipts.exists()

    with pytest.raises(ValueError, match="(?i)(receipt|repository)"):
        provider_smoke.run_smoke(
            profile=profile,
            mode=RunMode.REPLAY,
            nonce="stage0-5-test-nonce",
            receipts_root=repo_receipts,
        )

    assert not repo_receipts.exists()


def test_existing_insecure_receipt_root_is_rejected_before_live_calls(
    monkeypatch,
    tmp_path: Path,
):
    profile = provider_smoke.load_profile(_private_profile(tmp_path))
    receipts_root = tmp_path / "shared-receipts"
    receipts_root.mkdir(mode=0o755)
    receipts_root.chmod(0o755)
    calls = []

    def transport_must_not_be_built(binding):
        calls.append(binding.provider)
        raise AssertionError("transport built before receipt preflight")

    monkeypatch.setattr(provider_smoke, "_transport", transport_must_not_be_built)

    with pytest.raises(ValueError, match="(?i)(owner|0700)"):
        provider_smoke.run_smoke(
            profile=profile,
            mode=RunMode.LIVE,
            nonce="stage0-5-test-nonce",
            receipts_root=receipts_root,
        )

    assert calls == []
    assert stat.S_IMODE(receipts_root.stat().st_mode) == 0o755


@pytest.mark.parametrize(
    ("failure_kind", "response_observed"),
    [
        ("transport_error", False),
        ("invalid_json", True),
        ("invalid_provenance", True),
        ("wrong_nonce", True),
    ],
)
def test_failure_receipts_distinguish_transport_attempt_from_response(
    monkeypatch,
    tmp_path: Path,
    failure_kind: str,
    response_observed: bool,
):
    profile = provider_smoke.load_profile(_private_profile(tmp_path))
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(
        "evals.campaigns.gemma_needle_2000_v1.provider_adapters.default_cache_root",
        lambda: cache_root,
    )

    def fake_transport(binding):
        def call(request, settings):
            if binding.provider == "vertex":
                if failure_kind == "transport_error":
                    raise RuntimeError("synthetic transport failure")
                if failure_kind == "invalid_json":
                    return {
                        "content": "not-json",
                        "transport_receipt": _provider_receipt(binding),
                    }
                if failure_kind == "wrong_nonce":
                    return {
                        "content": (
                            '{"probe":"provider-contract-v1",'
                            '"nonce":"different-live-nonce"}'
                        ),
                        "transport_receipt": _provider_receipt(binding),
                    }
                receipt = _provider_receipt(binding)
                receipt["auth_kind"] = "wrong"
                return {
                    "content": (
                        '{"probe":"provider-contract-v1",'
                        '"nonce":"stage0-5-test-nonce"}'
                    ),
                    "transport_receipt": receipt,
                }
            return {
                "content": (
                    '{"probe":"provider-contract-v1",'
                    '"nonce":"stage0-5-test-nonce"}'
                ),
                "transport_receipt": _provider_receipt(binding),
            }

        return call

    monkeypatch.setattr(provider_smoke, "_transport", fake_transport)
    summary, passed = provider_smoke.run_smoke(
        profile=profile,
        mode=RunMode.LIVE,
        nonce="stage0-5-test-nonce",
        receipts_root=tmp_path / "receipts",
    )
    assert passed is False
    receipt = json.loads(Path(summary["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["evidence_class"] == "live_transport_contract_failed"
    vertex = next(item for item in receipt["providers"] if item["provider"] == "vertex")
    assert vertex["transport_invocation_attempts_current_run"] == 1
    assert vertex["transport_response_observed_current_run"] is response_observed
    assert vertex["evidence_class"] == "live_transport_contract_failed"


def test_profile_and_receipts_reject_a_sibling_git_workspace(tmp_path: Path):
    profile = provider_smoke.load_profile(_private_profile(tmp_path))
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir(mode=0o700)
    (other_repo / ".git").mkdir(mode=0o700)
    private = other_repo / "private"
    private.mkdir(mode=0o700)
    profile_path = private / "providers.json"
    profile_path.write_text(json.dumps(_profile_payload()), encoding="utf-8")
    profile_path.chmod(0o600)

    with pytest.raises(ValueError, match="(?i)(profile|repository)"):
        provider_smoke.load_profile(profile_path)
    with pytest.raises(ValueError, match="(?i)(receipt|repository)"):
        provider_smoke.run_smoke(
            profile=profile,
            mode=RunMode.REPLAY,
            nonce="stage0-5-test-nonce",
            receipts_root=other_repo / "receipts",
        )
