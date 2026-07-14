from __future__ import annotations

import concurrent.futures
import inspect
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from xai_sdk import Client as SDKClient

from src import utils
from src.provider_harvest import XAIWorkerEpisodeUploader


def test_inference_factory_constructs_with_only_inference_authority(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("xai_sdk.Client", FakeClient)
    monkeypatch.setattr(utils, "XAI_API_KEY", "inference-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "management-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_KEY", "sdk-management-test-key")
    monkeypatch.setattr(utils, "_client", None)

    inference = utils.get_xai_inference_client()

    assert utils.get_xai_client() is inference
    assert calls == [
        {
            "api_key": "inference-test-key",
            "management_api_key": utils._XAI_INFERENCE_MANAGEMENT_ISOLATION_KEY,
        }
    ]
    assert getattr(inference, "collections", None) is None


def test_real_sdk_inference_constructor_never_uses_management_environment(
    monkeypatch,
):
    channels = []

    def capture_channel(self, api_key, api_host, *args, **kwargs):
        channels.append((api_key, api_host))
        return MagicMock()

    monkeypatch.setattr(SDKClient, "_make_grpc_channel", capture_channel)
    monkeypatch.setattr(utils, "XAI_API_KEY", "inference-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "canonical-management-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_KEY", "sdk-management-test-key")
    monkeypatch.setattr(utils, "_client", None)

    inference = utils.get_xai_inference_client()

    assert channels == [
        ("inference-test-key", "api.x.ai"),
        (
            utils._XAI_INFERENCE_MANAGEMENT_ISOLATION_KEY,
            "management-api.x.ai",
        ),
    ]
    assert all(key != "sdk-management-test-key" for key, _ in channels)
    assert all(key != "canonical-management-test-key" for key, _ in channels)
    assert getattr(inference, "collections", None) is None
    utils.close_xai_inference_client()


def test_management_factory_constructs_with_both_sdk_credentials(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("xai_sdk.Client", FakeClient)
    monkeypatch.setattr(utils, "XAI_API_KEY", "inference-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "management-test-key")
    monkeypatch.delenv("XAI_MANAGEMENT_KEY", raising=False)
    monkeypatch.setattr(utils, "_management_client", None)

    management = utils.get_xai_management_client()

    assert management is utils.get_xai_management_client()
    assert calls == [
        {
            "api_key": "inference-test-key",
            "management_api_key": "management-test-key",
        }
    ]


@pytest.mark.parametrize(
    ("inference_key", "management_key", "message"),
    [
        ("", "management-test-key", "XAI_API_KEY"),
        ("inference-test-key", "", "XAI_MANAGEMENT_API_KEY"),
    ],
)
def test_management_factory_requires_both_credentials(
    monkeypatch, inference_key, management_key, message
):
    constructed = False

    class ForbiddenClient:
        def __init__(self, **kwargs):
            nonlocal constructed
            constructed = True

    monkeypatch.setattr("xai_sdk.Client", ForbiddenClient)
    monkeypatch.setattr(utils, "XAI_API_KEY", inference_key)
    if management_key:
        monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", management_key)
    else:
        monkeypatch.delenv("XAI_MANAGEMENT_API_KEY", raising=False)
    monkeypatch.delenv("XAI_MANAGEMENT_KEY", raising=False)
    monkeypatch.setattr(utils, "_management_client", None)

    with pytest.raises(ValueError, match=message):
        utils.get_xai_management_client()
    assert constructed is False


@pytest.mark.parametrize(
    ("canonical", "sdk_alias", "expected"),
    [
        ("canonical-test-key", "", "canonical-test-key"),
        ("", "sdk-alias-test-key", "sdk-alias-test-key"),
        ("shared-test-key", "shared-test-key", "shared-test-key"),
    ],
)
def test_management_factory_resolves_aliases_deterministically(
    monkeypatch, canonical, sdk_alias, expected
):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("xai_sdk.Client", FakeClient)
    monkeypatch.setattr(utils, "XAI_API_KEY", "inference-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", canonical)
    monkeypatch.setenv("XAI_MANAGEMENT_KEY", sdk_alias)
    monkeypatch.setattr(utils, "_management_client", None)

    utils.get_xai_management_client()

    assert calls == [
        {
            "api_key": "inference-test-key",
            "management_api_key": expected,
        }
    ]


def test_management_factory_rejects_conflicting_aliases_before_construction(
    monkeypatch,
):
    constructor = MagicMock()
    monkeypatch.setattr("xai_sdk.Client", constructor)
    monkeypatch.setattr(utils, "XAI_API_KEY", "inference-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "canonical-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_KEY", "different-sdk-alias-test-key")
    monkeypatch.setattr(utils, "_management_client", None)

    with pytest.raises(ValueError, match="both configured"):
        utils.get_xai_management_client()

    constructor.assert_not_called()


def test_eval_recording_wraps_inference_but_never_management(monkeypatch):
    clients = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            clients.append(self)

    monkeypatch.setattr("xai_sdk.Client", FakeClient)
    monkeypatch.setattr(utils, "XAI_API_KEY", "inference-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "management-test-key")
    monkeypatch.delenv("XAI_MANAGEMENT_KEY", raising=False)
    monkeypatch.setenv("UNIGROK_EVAL_RECORD", "1")
    monkeypatch.setattr(utils, "_client", None)
    monkeypatch.setattr(utils, "_management_client", None)

    inference = utils.get_xai_inference_client()
    management = utils.get_xai_management_client()

    assert isinstance(inference, utils._EvalRecordingClient)
    assert inference._client._delegate is clients[0]
    assert management is clients[1]
    assert not isinstance(management, utils._EvalRecordingClient)


def test_role_specific_close_functions_do_not_cross_caches(monkeypatch):
    inference = SimpleNamespace(close=MagicMock())
    management = SimpleNamespace(close=MagicMock())
    monkeypatch.setattr(utils, "_client", inference)
    monkeypatch.setattr(utils, "_management_client", management)

    utils.close_xai_inference_client()

    inference.close.assert_called_once_with()
    management.close.assert_not_called()
    assert utils._client is None
    assert utils._management_client is management

    utils.close_xai_management_client()

    management.close.assert_called_once_with()
    assert utils._management_client is None


def test_compatibility_close_hook_closes_both_role_caches(monkeypatch):
    inference = SimpleNamespace(close=MagicMock())
    management = SimpleNamespace(close=MagicMock())
    monkeypatch.setattr(utils, "_client", inference)
    monkeypatch.setattr(utils, "_management_client", management)

    utils.close_xai_client()

    inference.close.assert_called_once_with()
    management.close.assert_called_once_with()
    assert utils._client is None
    assert utils._management_client is None


def test_management_factory_is_thread_safe_and_separate_from_inference(monkeypatch):
    calls = []

    class SlowClient:
        def __init__(self, **kwargs):
            time.sleep(0.01)
            self.kwargs = kwargs
            calls.append(self)

    monkeypatch.setattr("xai_sdk.Client", SlowClient)
    monkeypatch.setattr(utils, "XAI_API_KEY", "inference-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "management-test-key")
    monkeypatch.delenv("XAI_MANAGEMENT_KEY", raising=False)
    monkeypatch.setattr(utils, "_client", None)
    monkeypatch.setattr(utils, "_management_client", None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        management_clients = list(
            pool.map(lambda _: utils.get_xai_management_client(), range(8))
        )
    inference = utils.get_xai_inference_client()

    assert len(calls) == 2
    assert all(client is management_clients[0] for client in management_clients)
    assert management_clients[0].kwargs == {
        "api_key": "inference-test-key",
        "management_api_key": "management-test-key",
    }
    assert inference.kwargs == {
        "api_key": "inference-test-key",
        "management_api_key": utils._XAI_INFERENCE_MANAGEMENT_ISOLATION_KEY,
    }
    assert inference is not management_clients[0]


def test_management_factory_is_confined_to_approved_admin_modules():
    root = Path(__file__).resolve().parents[1]
    users = {
        path.relative_to(root).as_posix()
        for path in (root / "src").rglob("*.py")
        if "get_xai_management_client" in path.read_text(encoding="utf-8")
    }

    assert users == {"src/provider_harvest.py", "src/rag.py", "src/utils.py"}
    assert (
        inspect.signature(XAIWorkerEpisodeUploader).parameters["client_factory"].default
        is utils.get_xai_management_client
    )
