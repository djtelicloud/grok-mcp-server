from __future__ import annotations

from types import SimpleNamespace

import pytest

from unigrok_public import xai_api


@pytest.mark.asyncio
async def test_zero_byte_file_content_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty_metadata(_file_id: str) -> dict[str, object]:
        return {"file_id": "empty", "size_bytes": 0}

    monkeypatch.setattr(xai_api, "get_file", empty_metadata)
    result = await xai_api.get_file_content("empty", max_bytes=100)

    assert result["content"] == ""
    assert result["bytes_returned"] == 0
    assert result["total_bytes"] == 0
    assert result["truncated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "message"),
    [(None, "missing"), (-1, "negative")],
)
async def test_unsafe_file_sizes_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    size: int | None,
    message: str,
) -> None:
    async def unsafe_metadata(_file_id: str) -> dict[str, object]:
        return {"file_id": "unsafe", "size_bytes": size}

    monkeypatch.setattr(xai_api, "get_file", unsafe_metadata)
    with pytest.raises(ValueError, match=message):
        await xai_api.get_file_content("unsafe", max_bytes=100)


def test_file_metadata_preserves_zero_and_distinguishes_missing() -> None:
    assert xai_api._file_metadata(SimpleNamespace(size=0))["size_bytes"] == 0
    assert xai_api._file_metadata(SimpleNamespace())["size_bytes"] is None
    assert xai_api._file_metadata(SimpleNamespace(size=-1))["size_bytes"] == -1
