"""Media domain — validation + generation/upload plans (Phase 3 SRP)."""
from __future__ import annotations

import base64
import ipaddress
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

FILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def validated_file_id(value: str) -> str:
    file_id = str(value or "").strip()
    if not FILE_ID_PATTERN.fullmatch(file_id):
        raise ValueError("file_id contains unsupported characters")
    return file_id


def validated_media_url(value: str, field: str) -> str:
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise ValueError(f"{field} must be a public https URL")
    host = parts.hostname or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        if host == "localhost" or host.endswith((".localhost", ".local")):
            raise ValueError(f"{field} must be a public https URL") from exc
    else:
        if not address.is_global:
            raise ValueError(f"{field} must be a public https URL")
    return url


def validated_media_urls(
    values: Sequence[str] | None, field: str, maximum: int
) -> list[str]:
    items = list(values or [])
    if len(items) > maximum:
        raise ValueError(f"{field} accepts at most {maximum} URLs")
    return [validated_media_url(value, field) for value in items]


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
