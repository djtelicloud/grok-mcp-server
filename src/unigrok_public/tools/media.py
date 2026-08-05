"""Media domain — validation + generation/upload plans (Phase 3 SRP)."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import re
import socket
import threading
import weakref
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

FILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
MEDIA_URL_MAX_CHARS = 4_096
MEDIA_DNS_TIMEOUT_SECONDS = 2.0
MEDIA_DNS_TOTAL_TIMEOUT_SECONDS = 5.0
MEDIA_DNS_CONCURRENCY = 4
_MEDIA_INTERNAL_SUFFIXES = ("localhost", "local", "internal", "home.arpa")
_MEDIA_DNS_LIMITER_LOCK = threading.Lock()
_MEDIA_DNS_LIMITERS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = weakref.WeakKeyDictionary()
_MEDIA_DNS_TASKS: set[asyncio.Task[None]] = set()

MediaResolver = Callable[[str, int], Awaitable[Sequence[str]]]


def validated_file_id(value: str) -> str:
    file_id = str(value or "").strip()
    if not FILE_ID_PATTERN.fullmatch(file_id):
        raise ValueError("file_id contains unsupported characters")
    return file_id


@dataclass(frozen=True)
class _MediaTarget:
    url: str
    host: str
    port: int
    literal_address: ipaddress.IPv4Address | ipaddress.IPv6Address | None


def _is_public_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return address.is_global and not address.is_multicast


def _validated_media_target(value: str, field: str) -> _MediaTarget:
    url = str(value or "").strip()
    if not url or len(url) > MEDIA_URL_MAX_CHARS or "\\" in url or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in url
    ):
        raise ValueError(f"{field} must be a public https URL")
    try:
        url.encode("utf-8")
        parts = urlsplit(url)
        host = str(parts.hostname or "").rstrip(".")
        parsed_port = parts.port
        port = 443 if parsed_port is None else parsed_port
        username = parts.username
        password = parts.password
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{field} must be a public https URL") from exc
    if (
        parts.scheme.lower() != "https"
        or not parts.netloc
        or username is not None
        or password is not None
        or not host
        or "%" in host
        or not 1 <= port <= 65_535
    ):
        raise ValueError(f"{field} must be a public https URL")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            normalized_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError(f"{field} must be a public https URL") from exc
        if (
            not normalized_host
            or len(normalized_host) > 253
            or "." not in normalized_host
            or any(
                normalized_host == suffix or normalized_host.endswith(f".{suffix}")
                for suffix in _MEDIA_INTERNAL_SUFFIXES
            )
        ):
            raise ValueError(f"{field} must be a public https URL") from None
        return _MediaTarget(url, normalized_host, port, None)
    if not _is_public_address(address):
        raise ValueError(f"{field} must be a public https URL")
    return _MediaTarget(url, address.compressed, port, address)


def validated_media_url(value: str, field: str) -> str:
    return _validated_media_target(value, field).url


def validated_media_urls(
    values: Sequence[str] | None, field: str, maximum: int
) -> list[str]:
    items = list(values or [])
    if len(items) > maximum:
        raise ValueError(f"{field} accepts at most {maximum} URLs")
    return [validated_media_url(value, field) for value in items]


def _media_dns_limiter() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    with _MEDIA_DNS_LIMITER_LOCK:
        limiter = _MEDIA_DNS_LIMITERS.get(loop)
        if limiter is None:
            limiter = asyncio.Semaphore(MEDIA_DNS_CONCURRENCY)
            _MEDIA_DNS_LIMITERS[loop] = limiter
    return limiter


async def _resolve_media_host(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    answers = await loop.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(str(answer[4][0]) for answer in answers)


async def _validate_resolved_target(
    target: _MediaTarget,
    field: str,
    resolver: MediaResolver,
) -> None:
    if target.literal_address is not None:
        return
    try:
        answers = await resolver(target.host, target.port)
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"{field} must be a public https URL") from exc
    if not answers:
        raise ValueError(f"{field} must be a public https URL")
    try:
        addresses = tuple(ipaddress.ip_address(str(answer)) for answer in answers)
    except ValueError as exc:
        raise ValueError(f"{field} must be a public https URL") from exc
    if any(not _is_public_address(address) for address in addresses):
        raise ValueError(f"{field} must be a public https URL")


def _consume_media_dns_task(task: asyncio.Task[None]) -> None:
    _MEDIA_DNS_TASKS.discard(task)
    if task.cancelled():
        return
    with contextlib.suppress(Exception):
        task.exception()


async def _validate_target_with_slot(
    target: _MediaTarget,
    field: str,
    resolver: MediaResolver,
    limiter: asyncio.Semaphore,
    timeout_seconds: float,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    try:
        await asyncio.wait_for(limiter.acquire(), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise ValueError(f"{field} must be a public https URL") from exc
    remaining = deadline - loop.time()
    if remaining <= 0:
        limiter.release()
        raise ValueError(f"{field} must be a public https URL")

    async def resolve_and_release() -> None:
        try:
            await _validate_resolved_target(target, field, resolver)
        finally:
            limiter.release()

    worker = asyncio.create_task(resolve_and_release())
    _MEDIA_DNS_TASKS.add(worker)
    worker.add_done_callback(_consume_media_dns_task)
    done, _ = await asyncio.wait({worker}, timeout=remaining)
    if not done:
        raise ValueError(f"{field} must be a public https URL")
    await worker


async def validated_public_media_urls(
    values: Sequence[str] | None,
    field: str,
    maximum: int,
    *,
    resolver: MediaResolver | None = None,
    timeout_seconds: float = MEDIA_DNS_TIMEOUT_SECONDS,
    total_timeout_seconds: float = MEDIA_DNS_TOTAL_TIMEOUT_SECONDS,
) -> list[str]:
    items = list(values or [])
    if len(items) > maximum:
        raise ValueError(f"{field} accepts at most {maximum} URLs")
    targets = [_validated_media_target(value, field) for value in items]
    unique_targets = {
        (target.host, target.port): target
        for target in targets
        if target.literal_address is None
    }
    if unique_targets:
        active_resolver = resolver or _resolve_media_host
        limiter = _media_dns_limiter()

        async def validate(target: _MediaTarget) -> None:
            await _validate_target_with_slot(
                target, field, active_resolver, limiter, timeout_seconds
            )

        async def validate_all() -> None:
            tasks = [
                asyncio.create_task(validate(target))
                for target in unique_targets.values()
            ]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        try:
            await asyncio.wait_for(validate_all(), timeout=total_timeout_seconds)
        except TimeoutError as exc:
            raise ValueError(f"{field} must be a public https URL") from exc
    return [target.url for target in targets]


async def validated_public_media_url(
    value: str,
    field: str,
    *,
    resolver: MediaResolver | None = None,
    timeout_seconds: float = MEDIA_DNS_TIMEOUT_SECONDS,
    total_timeout_seconds: float = MEDIA_DNS_TOTAL_TIMEOUT_SECONDS,
) -> str:
    return (
        await validated_public_media_urls(
            [value],
            field,
            1,
            resolver=resolver,
            timeout_seconds=timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
        )
    )[0]


def validated_image_count(n: int) -> int:
    count = int(n)
    if not 1 <= count <= 10:
        raise ValueError("n must be between 1 and 10")
    return count


def validated_video_duration(duration: int | None, *, lo: int, hi: int) -> int | None:
    if duration is None:
        return None
    d = int(duration)
    if not lo <= d <= hi:
        raise ValueError(f"duration must be between {lo} and {hi} seconds")
    return d


def validated_upload_filename(filename: str) -> str:
    safe_name = str(filename or "").strip()
    if not safe_name or Path(safe_name).name != safe_name or len(safe_name) > 255:
        raise ValueError("filename must be a plain filename without path components")
    return safe_name


def decode_upload_content(content_base64: str, *, max_bytes: int) -> bytes:
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("content_base64 is not valid base64") from exc
    if not content or len(content) > max_bytes:
        raise ValueError(f"decoded file must be between 1 and {max_bytes} bytes")
    return content


def clamp_expires_after(seconds: int) -> int:
    return max(3_600, min(int(seconds), 2_592_000))


def clamp_file_content_limit(max_bytes: int) -> int:
    return max(1_024, min(int(max_bytes), 1_000_000))


def clamp_list_files_limit(limit: int) -> int:
    return max(1, min(int(limit), 100))


def require_confirm_delete(confirm_delete: bool, *, what: str) -> None:
    if confirm_delete is not True:
        raise ValueError(f"Permanently deleting {what} requires confirm_delete=true")


@dataclass(frozen=True)
class ImageGenPlan:
    prompt: str
    model: str
    image_urls: list[str]
    n: int
    aspect_ratio: str | None
    resolution: str | None


@dataclass(frozen=True)
class VideoGenPlan:
    prompt: str
    model: str
    image_url: str | None
    video_url: str | None
    reference_image_urls: list[str]
    duration: int | None
    aspect_ratio: str | None
    resolution: str | None


@dataclass(frozen=True)
class VideoExtendPlan:
    prompt: str
    model: str
    video_url: str
    duration: int | None


@dataclass(frozen=True)
class UploadPlan:
    filename: str
    content: bytes
    expires_after_seconds: int


def pick_image_model(catalogs: dict[str, Any]) -> str:
    image_models = [
        str(item["id"])
        for item in (catalogs.get("api") or {}).get("image_models", [])
        if item.get("id")
    ]
    if not image_models:
        raise RuntimeError("The provider returned no image-generation model")
    return image_models[0]


def plan_generate_image(
    *,
    prompt: str,
    image_urls: list[str] | None,
    n: int,
    aspect_ratio: str | None,
    resolution: str | None,
    catalogs: dict[str, Any],
    validate_prompt: Callable[[str, str], str],
) -> ImageGenPlan:
    return ImageGenPlan(
        prompt=validate_prompt(prompt, "prompt"),
        model=pick_image_model(catalogs),
        image_urls=validated_media_urls(image_urls, "image_urls", 10),
        n=validated_image_count(n),
        aspect_ratio=str(aspect_ratio).strip() if aspect_ratio else None,
        resolution=resolution,
    )


def plan_generate_video(
    *,
    prompt: str,
    image_url: str | None,
    video_url: str | None,
    reference_image_urls: list[str] | None,
    duration: int | None,
    aspect_ratio: str | None,
    resolution: str | None,
    validate_prompt: Callable[[str, str], str],
    model: str = "grok-imagine-video",
) -> VideoGenPlan:
    if image_url and video_url:
        raise ValueError("provide image_url or video_url, not both")
    return VideoGenPlan(
        prompt=validate_prompt(prompt, "prompt"),
        model=model,
        image_url=validated_media_url(image_url, "image_url") if image_url else None,
        video_url=validated_media_url(video_url, "video_url") if video_url else None,
        reference_image_urls=validated_media_urls(
            reference_image_urls, "reference_image_urls", 10
        ),
        duration=validated_video_duration(duration, lo=1, hi=15),
        aspect_ratio=str(aspect_ratio).strip() if aspect_ratio else None,
        resolution=resolution,
    )


def plan_extend_video(
    *,
    prompt: str,
    video_url: str,
    duration: int | None,
    validate_prompt: Callable[[str, str], str],
    model: str = "grok-imagine-video",
) -> VideoExtendPlan:
    return VideoExtendPlan(
        prompt=validate_prompt(prompt, "prompt"),
        model=model,
        video_url=validated_media_url(video_url, "video_url"),
        duration=validated_video_duration(duration, lo=2, hi=10),
    )


def plan_upload_file(
    *,
    filename: str,
    content_base64: str,
    expires_after_seconds: int,
    max_bytes: int,
) -> UploadPlan:
    return UploadPlan(
        filename=validated_upload_filename(filename),
        content=decode_upload_content(content_base64, max_bytes=max_bytes),
        expires_after_seconds=clamp_expires_after(expires_after_seconds),
    )
