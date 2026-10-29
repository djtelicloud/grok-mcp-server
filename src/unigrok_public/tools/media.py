"""Media domain — validation + generation/upload plans (Phase 3 SRP)."""

from __future__ import annotations

import base64
import ipaddress
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

FILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MAX_MEDIA_URL_CHARS = 8_192

# Special-use and conventional private DNS suffixes are rejected lexically. This is
# deliberately not presented as a complete DNS policy: the xAI provider dereferences
# media URLs later using its own resolver and network boundary.
NON_PUBLIC_DOMAIN_SUFFIXES = frozenset(
    {
        "alt",
        "corp",
        "example",
        "home",
        "home.arpa",
        "internal",
        "intranet",
        "invalid",
        "lan",
        "local",
        "localdomain",
        "localhost",
        "onion",
        "private",
        "test",
    }
)


def validated_file_id(value: str) -> str:
    file_id = str(value or "").strip()
    if not FILE_ID_PATTERN.fullmatch(file_id):
        raise ValueError("file_id contains unsupported characters")
    return file_id


def _invalid_media_url(field: str) -> ValueError:
    return ValueError(f"{field} must be a public https URL")


def _normalized_public_domain(host: str, field: str) -> str:
    if not host or "%" in host:
        raise _invalid_media_url(field)

    # One root-label dot is harmless and normalized away. More than one creates an
    # empty DNS label and is rejected.
    candidate = host[:-1] if host.endswith(".") else host
    if not candidate or candidate.endswith("."):
        raise _invalid_media_url(field)

    try:
        normalized = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise _invalid_media_url(field) from exc

    # IDNA maps several Unicode dot characters to ASCII dots. Normalize one final
    # root-label dot after that mapping as well, while still rejecting empty labels.
    normalized = normalized[:-1] if normalized.endswith(".") else normalized
    if not normalized or normalized.endswith(".") or len(normalized) > 253:
        raise _invalid_media_url(field)

    # All IP literals are excluded, not only private addresses. Alternate IPv4 forms
    # that ipaddress intentionally does not recognize are rejected by the strict DNS
    # label/TLD checks below (for example 127.1, 2130706433, and 0x7f000001).
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        raise _invalid_media_url(field)

    labels = normalized.split(".")
    if len(labels) < 2 or any(not DOMAIN_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise _invalid_media_url(field)
    for label in labels:
        if not label.startswith("xn--"):
            continue
        try:
            decoded_label = label.encode("ascii").decode("idna")
            round_trip = decoded_label.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise _invalid_media_url(field) from exc
        if round_trip != label:
            raise _invalid_media_url(field)
    top_level = labels[-1]
    if not (top_level.isalpha() or (top_level.startswith("xn--") and len(top_level) > 4)):
        raise _invalid_media_url(field)
    if any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in NON_PUBLIC_DOMAIN_SUFFIXES
    ):
        raise _invalid_media_url(field)
    return normalized


def _normalized_domain_allowlist(allowed_domains: Sequence[str], field: str) -> frozenset[str]:
    raw_domains = [allowed_domains] if isinstance(allowed_domains, str) else allowed_domains
    normalized: set[str] = set()
    for value in raw_domains:
        domain = str(value or "").strip()
        if "://" in domain or any(character in domain for character in "/?#@"):
            raise ValueError("allowed_domains must contain plain public domain names")
        try:
            normalized.add(_normalized_public_domain(domain, field))
        except ValueError as exc:
            raise ValueError("allowed_domains must contain plain public domain names") from exc
    return frozenset(normalized)


def validated_media_domains(values: Sequence[str]) -> list[str]:
    """Validate an operator allowlist without performing DNS or network I/O."""
    return sorted(_normalized_domain_allowlist(values, "allowed_domains"))


def validated_media_url(
    value: str,
    field: str,
    *,
    allowed_domains: Sequence[str] | None = None,
) -> str:
    """Normalize and lexically validate a provider-fetched media URL.

    UniGrok does not dereference these URLs. The xAI provider resolves them later, so
    local DNS lookup cannot pin the destination or establish DNS-rebinding safety. This
    validator therefore performs no network I/O; it rejects ambiguous URL syntax, IP
    literals, and obvious non-public DNS names. An explicit domain allowlist can narrow
    the accepted public-domain syntax further.
    """
    url = str(value or "").strip()
    if (
        not url
        or len(url) > MAX_MEDIA_URL_CHARS
        or any(
            character == "\\"
            or character.isspace()
            or ord(character) < 0x20
            or ord(character) == 0x7F
            for character in url
        )
    ):
        raise _invalid_media_url(field)
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError as exc:
        raise _invalid_media_url(field) from exc
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or "%" in parts.netloc
        or "[" in parts.netloc
        or "]" in parts.netloc
        or parts.netloc.endswith(":")
        or port == 0
    ):
        raise _invalid_media_url(field)

    normalized_host = _normalized_public_domain(host, field)
    if allowed_domains is not None:
        normalized_allowlist = _normalized_domain_allowlist(allowed_domains, field)
        if not any(
            normalized_host == domain or normalized_host.endswith(f".{domain}")
            for domain in normalized_allowlist
        ):
            raise ValueError(f"{field} host is not in allowed_domains")

    authority = normalized_host
    if port is not None and port != 443:
        authority = f"{authority}:{port}"
    return urlunsplit(("https", authority, parts.path or "/", parts.query, ""))


def validated_media_urls(
    values: Sequence[str] | None,
    field: str,
    maximum: int,
    *,
    allowed_domains: Sequence[str] | None = None,
) -> list[str]:
    items = list(values or [])
    if len(items) > maximum:
        raise ValueError(f"{field} accepts at most {maximum} URLs")
    return [validated_media_url(value, field, allowed_domains=allowed_domains) for value in items]


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
    allowed_domains: Sequence[str] | None = None,
) -> ImageGenPlan:
    return ImageGenPlan(
        prompt=validate_prompt(prompt, "prompt"),
        model=pick_image_model(catalogs),
        image_urls=validated_media_urls(
            image_urls, "image_urls", 10, allowed_domains=allowed_domains
        ),
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
    allowed_domains: Sequence[str] | None = None,
    model: str = "grok-imagine-video",
) -> VideoGenPlan:
    if image_url and video_url:
        raise ValueError("provide image_url or video_url, not both")
    return VideoGenPlan(
        prompt=validate_prompt(prompt, "prompt"),
        model=model,
        image_url=(
            validated_media_url(image_url, "image_url", allowed_domains=allowed_domains)
            if image_url
            else None
        ),
        video_url=(
            validated_media_url(video_url, "video_url", allowed_domains=allowed_domains)
            if video_url
            else None
        ),
        reference_image_urls=validated_media_urls(
            reference_image_urls,
            "reference_image_urls",
            10,
            allowed_domains=allowed_domains,
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
    allowed_domains: Sequence[str] | None = None,
    model: str = "grok-imagine-video",
) -> VideoExtendPlan:
    return VideoExtendPlan(
        prompt=validate_prompt(prompt, "prompt"),
        model=model,
        video_url=validated_media_url(
            video_url, "video_url", allowed_domains=allowed_domains
        ),
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
