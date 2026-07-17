from types import SimpleNamespace

import pytest

from unigrok_public import xai_api


class FakeConversation:
    def __init__(self) -> None:
        self.messages = []

    def append(self, message) -> None:
        self.messages.append(message)

    def sample(self):
        return SimpleNamespace(
            content="API_OK",
            model="grok-api-live",
            finish_reason="final_answer",
            id="response_test",
            cost_usd=0.01,
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1),
            citations=["https://example.com/source"],
            tool_outputs=[],
        )


class FakeChat:
    def __init__(self) -> None:
        self.params = None
        self.conversation = FakeConversation()

    def create(self, **params):
        self.params = params
        return self.conversation


class FakeFiles:
    def __init__(self) -> None:
        self.deleted = None

    def upload(self, content, **params):
        assert content == b"hello"
        assert params["filename"] == "note.txt"
        return SimpleNamespace(id="file_test", filename="note.txt", size=5)

    def list(self, **params):
        assert params == {"limit": 10}
        return SimpleNamespace(data=[SimpleNamespace(id="file_test", filename="note.txt", size=5)])

    def get(self, file_id):
        assert file_id == "file_test"
        return SimpleNamespace(id=file_id, filename="note.txt", size=5)

    def content(self, file_id):
        assert file_id == "file_test"
        return b"hello"

    def delete(self, file_id):
        self.deleted = file_id


class FakeClient:
    def __init__(self) -> None:
        self.closed = False
        self.chat = FakeChat()
        self.models = SimpleNamespace(
            list_language_models=lambda: [
                SimpleNamespace(name="grok-api-live", max_prompt_length=123_000),
                SimpleNamespace(name="grok-api-new", max_prompt_length=456_000),
            ],
            list_image_generation_models=lambda: [
                SimpleNamespace(name="grok-image-live"),
                SimpleNamespace(name="grok-image-new"),
            ],
        )
        self.image_params = None
        self.image = SimpleNamespace(sample_batch=self._sample_batch)
        self.video_params = None
        self.video = SimpleNamespace(generate=self._generate_video, extend=self._extend_video)
        self.files = FakeFiles()

    def _sample_batch(self, **params):
        self.image_params = params
        return [SimpleNamespace(url="https://example.com/image.png", cost_usd=0.02)]

    def _generate_video(self, **params):
        self.video_params = params
        return SimpleNamespace(url="https://example.com/video.mp4", duration=4, cost_usd=0.03)

    def _extend_video(self, **params):
        self.video_params = params
        return SimpleNamespace(url="https://example.com/extended.mp4", duration=6, cost_usd=0.04)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setenv("XAI_API_KEY", "test-only")
    monkeypatch.setattr(xai_api, "_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_probe_discovers_language_and_image_catalogs(fake_client: FakeClient) -> None:
    status = await xai_api.probe_models()
    assert [item["id"] for item in status["language_models"]] == [
        "grok-api-live",
        "grok-api-new",
    ]
    assert [item["id"] for item in status["image_models"]] == [
        "grok-image-live",
        "grok-image-new",
    ]
    assert status["ready"] is True
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_api_chat_returns_metered_receipt(fake_client: FakeClient) -> None:
    result = await xai_api.chat(
        "hello",
        model="grok-api-live",
        reasoning_effort="high",
        system_prompt="public boundary",
        allow_web=True,
        max_turns=3,
    )
    assert result["text"] == "API_OK"
    assert result["plane"] == "xai_api_key"
    assert result["billing_class"] == "metered_api"
    assert result["usage"]["total_tokens"] == 3
    assert fake_client.chat.params["model"] == "grok-api-live"
    assert fake_client.chat.params["reasoning_effort"] == "high"
    assert fake_client.chat.params["max_turns"] == 3
    assert len(fake_client.chat.params["tools"]) == 1


@pytest.mark.asyncio
async def test_media_parameters_are_forwarded_without_model_allowlist(
    fake_client: FakeClient,
) -> None:
    image = await xai_api.generate_image(
        "draw",
        model="provider-image-model-not-hard-coded",
        image_urls=[],
        n=1,
        aspect_ratio="1:1",
        resolution="1k",
    )
    assert image["images"][0]["url"] == "https://example.com/image.png"
    assert fake_client.image_params["model"] == "provider-image-model-not-hard-coded"

    video = await xai_api.generate_video(
        "move",
        model="provider-video-model-not-hard-coded",
        image_url=None,
        video_url=None,
        reference_image_urls=[],
        duration=4,
        aspect_ratio="16:9",
        resolution="720p",
    )
    assert video["video"]["duration_seconds"] == 4
    assert fake_client.video_params["model"] == "provider-video-model-not-hard-coded"

    extended = await xai_api.extend_video(
        "continue",
        model="provider-video-model-not-hard-coded",
        video_url="https://example.com/video.mp4",
        duration=2,
    )
    assert extended["video"]["duration_seconds"] == 6
    assert fake_client.video_params["video_url"] == "https://example.com/video.mp4"


@pytest.mark.asyncio
async def test_xai_file_lifecycle_wrappers(fake_client: FakeClient) -> None:
    uploaded = await xai_api.upload_file(b"hello", filename="note.txt", expires_after_seconds=3600)
    assert uploaded["file_id"] == "file_test"
    assert (await xai_api.list_files(10))["files"][0]["filename"] == "note.txt"
    assert (await xai_api.get_file("file_test"))["size_bytes"] == 5
    content = await xai_api.get_file_content("file_test", max_bytes=5)
    assert content["content"] == "hello"
    assert content["truncated"] is False
    assert (await xai_api.delete_file("file_test"))["deleted"] is True
    assert fake_client.files.deleted == "file_test"
