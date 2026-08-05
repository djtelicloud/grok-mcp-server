from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from unigrok_public import server
from unigrok_public.tools import media


@pytest.mark.parametrize(
    "url",
    [
        "https://service.internal/a.png",
        "https://service.home.arpa/a.png",
        "https://printer.local/a.png",
        "https://intranet/a.png",
        "https://user@example.com/a.png",
        "https://example.com:0/a.png",
        "https://example.com:99999/a.png",
        "https://example.com/\nheader",
        "https://example.com\\127.0.0.1/a.png",
        "https://[::ffff:127.0.0.1]/a.png",
        "https://example.com/\ud800",
    ],
)
def test_media_url_rejects_internal_and_ambiguous_targets(url: str) -> None:
    with pytest.raises(ValueError, match="public https URL"):
        media.validated_media_url(url, "image_url")


@pytest.mark.asyncio
async def test_media_dns_accepts_public_answers_and_deduplicates_hosts() -> None:
    calls: list[tuple[str, int]] = []

    async def resolver(host: str, port: int) -> Sequence[str]:
        calls.append((host, port))
        return ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]

    urls = await media.validated_public_media_urls(
        [
            "https://images.example.com/a.png",
            "https://images.example.com/b.png",
        ],
        "image_urls",
        10,
        resolver=resolver,
    )

    assert urls == [
        "https://images.example.com/a.png",
        "https://images.example.com/b.png",
    ]
    assert calls == [("images.example.com", 443)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [
        ["127.0.0.1"],
        ["93.184.216.34", "10.0.0.8"],
        ["169.254.169.254"],
        ["::1"],
        ["ff02::1"],
    ],
)
async def test_media_dns_rejects_any_non_public_answer(answers: list[str]) -> None:
    async def resolver(_host: str, _port: int) -> Sequence[str]:
        return answers

    with pytest.raises(ValueError, match="public https URL"):
        await media.validated_public_media_url(
            "https://images.example.com/a.png",
            "image_url",
            resolver=resolver,
        )


@pytest.mark.asyncio
async def test_media_dns_failure_and_timeout_fail_closed() -> None:
    async def failed(_host: str, _port: int) -> Sequence[str]:
        raise OSError("resolver unavailable")

    async def stalled(_host: str, _port: int) -> Sequence[str]:
        await asyncio.sleep(1)
        return ["93.184.216.34"]

    with pytest.raises(ValueError, match="public https URL"):
        await media.validated_public_media_url(
            "https://images.example.com/a.png",
            "image_url",
            resolver=failed,
        )
    with pytest.raises(ValueError, match="public https URL"):
        await media.validated_public_media_url(
            "https://images.example.com/a.png",
            "image_url",
            resolver=stalled,
            timeout_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_media_dns_concurrency_is_bounded_across_requests() -> None:
    active = 0
    maximum_active = 0

    async def resolver(_host: str, _port: int) -> Sequence[str]:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ["93.184.216.34"]

    first = [f"https://first-{index}.example.com/a.png" for index in range(6)]
    second = [f"https://second-{index}.example.com/a.png" for index in range(6)]
    assert await asyncio.gather(
        media.validated_public_media_urls(first, "image_urls", 10, resolver=resolver),
        media.validated_public_media_urls(second, "image_urls", 10, resolver=resolver),
    ) == [first, second]
    assert maximum_active == media.MEDIA_DNS_CONCURRENCY


@pytest.mark.asyncio
async def test_media_dns_failure_preserves_active_sibling_slot() -> None:
    slow_started = asyncio.Event()
    slow_release = asyncio.Event()
    slow_finished = asyncio.Event()

    async def resolver(host: str, _port: int) -> Sequence[str]:
        if host == "private.example.com":
            await slow_started.wait()
            return ["127.0.0.1"]
        slow_started.set()
        try:
            await slow_release.wait()
            return ["93.184.216.34"]
        finally:
            slow_finished.set()

    with pytest.raises(ValueError, match="public https URL"):
        await media.validated_public_media_urls(
            [
                "https://private.example.com/a.png",
                "https://slow.example.com/a.png",
            ],
            "image_urls",
            10,
            resolver=resolver,
        )
    assert not slow_finished.is_set()
    slow_release.set()
    await asyncio.wait_for(slow_finished.wait(), timeout=1)


@pytest.mark.asyncio
async def test_media_dns_timeout_retains_slots_until_resolver_work_finishes() -> None:
    release = asyncio.Event()
    all_started = asyncio.Event()
    active = 0
    started = 0

    async def stalled(_host: str, _port: int) -> Sequence[str]:
        nonlocal active, started
        active += 1
        started += 1
        if started == media.MEDIA_DNS_CONCURRENCY:
            all_started.set()
        try:
            await release.wait()
            return ["93.184.216.34"]
        finally:
            active -= 1

    requests = [
        asyncio.create_task(
            media.validated_public_media_url(
                f"https://stalled-{index}.example.com/a.png",
                "image_url",
                resolver=stalled,
                timeout_seconds=0.02,
                total_timeout_seconds=0.1,
            )
        )
        for index in range(media.MEDIA_DNS_CONCURRENCY)
    ]
    await asyncio.wait_for(all_started.wait(), timeout=1)
    results = await asyncio.gather(*requests, return_exceptions=True)
    assert all(isinstance(result, ValueError) for result in results)
    assert active == media.MEDIA_DNS_CONCURRENCY

    fifth_called = False

    async def fifth(_host: str, _port: int) -> Sequence[str]:
        nonlocal fifth_called
        fifth_called = True
        return ["93.184.216.34"]

    with pytest.raises(ValueError, match="public https URL"):
        await media.validated_public_media_url(
            "https://fifth.example.com/a.png",
            "image_url",
            resolver=fifth,
            timeout_seconds=0.02,
            total_timeout_seconds=0.1,
        )
    assert fifth_called is False

    release.set()
    for _ in range(100):
        if active == 0:
            break
        await asyncio.sleep(0.01)
    assert active == 0


@pytest.mark.asyncio
async def test_metered_media_tools_reject_private_dns_before_provider_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def private_resolver(_host: str, _port: int) -> Sequence[str]:
        return ["127.0.0.1"]

    monkeypatch.setattr(media, "_resolve_media_host", private_resolver)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)

    invocations = (
        lambda: server.chat_with_vision("inspect", ["https://media.example.com/a.png"]),
        lambda: server.generate_image(
            "edit", image_urls=["https://media.example.com/a.png"]
        ),
        lambda: server.generate_video(
            "animate", image_url="https://media.example.com/a.png"
        ),
        lambda: server.extend_video(
            "extend", video_url="https://media.example.com/a.mp4"
        ),
    )
    for invoke in invocations:
        with pytest.raises(ValueError, match="public https URL"):
            await invoke()
