# conftest.py — shared pytest fixtures for the uni-grok-mcp test suite
import os

# Set a dummy API key for all tests so utils.py loads without a real xAI credential
os.environ.setdefault("XAI_API_KEY", "xai-test-dummy-key-for-unit-tests")

# Flag to signal all modules that tests are running
os.environ["UNI_GROK_TESTING"] = "1"

import pytest
import src.principal_xai
import src.utils


def _reset_xai_client_state():
    if hasattr(src.utils, "_clients"):
        src.utils._clients.clear()
    else:
        src.utils._client = None  # legacy attribute name
    with src.principal_xai._CREDENTIAL_GENERATIONS_LOCK:
        src.principal_xai._CREDENTIAL_GENERATIONS.clear()

@pytest.fixture(scope="session", autouse=True)
def setup_test_env(tmp_path_factory):
    # Set up session-scoped temporary chats directory for database isolation
    tmp_dir = tmp_path_factory.mktemp("test_chats")
    os.environ["UNI_GROK_TEST_CHATS_DIR"] = str(tmp_dir)
    yield


@pytest.fixture(autouse=True)
def reset_global_client():
    _reset_xai_client_state()
    src.utils._management_client = None
    src.utils._MODEL_MAX_TOKENS_CACHE.clear()
    src.utils._BREAKER_STATE.clear()
    src.utils._ROUTING_ADVISOR.invalidate()
    src.utils._CALLER_SPEND_CACHE.clear()
    yield
    _reset_xai_client_state()
    src.utils._management_client = None
    src.utils._MODEL_MAX_TOKENS_CACHE.clear()
    src.utils._BREAKER_STATE.clear()
    src.utils._ROUTING_ADVISOR.invalidate()
    src.utils._CALLER_SPEND_CACHE.clear()


@pytest.fixture(scope="session", autouse=True)
async def cleanup_global_store(setup_test_env):
    yield
    from src.utils import store
    await store.close()
