# src/tools/media.py
# Decomposed Media generation tools for UniGrok MCP

import logging
from typing import Optional, List
from mcp.server.fastmcp import FastMCP
from ..models.results import MediaResult
from ..identity import get_active_principal, resolve_request_caller

from ..utils import (
    get_xai_client,
    encode_image_to_base64,
    encode_video_to_base64,
    GrokInvocationContext,
    register_internal_tool,
    PathResolver,
    run_blocking,
    input_limit,
    validate_local_input,
    enforce_caller_budget,
    store,
)

logger = logging.getLogger("GrokMCP")


async def _enforce_media_budget() -> Optional[str]:
    """Gate Imagine spend on UNIGROK_CALLER_BUDGETS before any xAI call."""
    caller = resolve_request_caller(None)
    budget_principal = get_active_principal() or caller
    await enforce_caller_budget(store, budget_principal)
    return caller


async def _record_media_telemetry(
    *,
    intent: str,
    route: str,
    cost_usd: float,
    model: str,
    latency_sec: float,
    caller: Optional[str],
) -> None:
    """Persist Imagine spend so caller budgets and rolls see direct media tools."""
    await store.save_telemetry(
        (intent or route)[:100],
        "API",
        1,
        float(latency_sec or 0.0),
        float(cost_usd or 0.0),
        caller=caller,
        model=model,
        tokens=0,
        token_kind="provider_exact",
        billing_source="xai_response_exact",
        routing={"route": route, "plane": "API"},
    )


async def generate_image(
    prompt: str,
    model: str = "grok-imagine-image",
    image_paths: Optional[List[str]] = None,
    image_urls: Optional[List[str]] = None,
    n: int = 1,
    image_format: str = "url",
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
) -> MediaResult:
    """Generate new images or edit existing ones with Grok Imagine.

    Args:
        prompt: Image description or edit instruction.
        model: Image model (`grok-imagine-image` or `grok-imagine-image-pro`).
        image_paths: Local image files used as edit sources or references.
        image_urls: Public image URLs used as edit sources or references.
        n: Number of images to generate (1–10).
        image_format: `"url"` (default) or `"base64"`.
        aspect_ratio: Aspect ratio like `"16:9"`, `"1:1"`, or `"9:16"`.
        resolution: `"1k"` or `"2k"`.

    Returns:
        MediaResult containing image metadata and URLs.
    """
    caller = await _enforce_media_budget()
    async with GrokInvocationContext(model, logger, append_signature=True) as ctx:
        if not 1 <= int(n) <= 10:
            raise ValueError("n must be between 1 and 10")
        params = {"model": model, "prompt": prompt, "n": n, "image_format": image_format}

        if aspect_ratio:
            params["aspect_ratio"] = aspect_ratio
        if resolution:
            params["resolution"] = resolution

        def _call_image():
            refs = []
            if image_paths:
                for path in image_paths:
                    resolved_path = PathResolver.validate_path(path)
                    validate_local_input(
                        resolved_path,
                        max_bytes=input_limit("UNIGROK_MAX_MEDIA_INPUT_BYTES", 20_000_000, 1_024, 100_000_000),
                        allowed_suffixes=(".jpg", ".jpeg", ".png", ".webp"),
                        label="image",
                    )
                    base64_string = encode_image_to_base64(
                        str(resolved_path),
                        max_bytes=input_limit("UNIGROK_MAX_MEDIA_INPUT_BYTES", 20_000_000, 1_024, 100_000_000),
                    )
                    ext = resolved_path.suffix.lower().replace('.', '')
                    refs.append(f"data:image/{ext};base64,{base64_string}")
            if image_urls:
                refs.extend(image_urls)

            if refs:
                params["image_urls"] = refs

            client = get_xai_client()
            res = client.image.sample_batch(**params)
            return res

        images = await run_blocking(_call_image, timeout=120.0)

        result = ["## Generated Image(s)\n\n"]
        for i, img in enumerate(images, 1):
            result.append(f"\n**Image {i}:** {img.url}\n\n")
            if img.prompt and img.prompt != prompt:
                result.append(f"*Revised prompt:* {img.prompt}\n\n")

        cost_usd = sum(float(getattr(img, "cost_usd", 0.0) or 0.0) for img in images)
        summary_text = ctx.format_output("".join(result), images)
        await _record_media_telemetry(
            intent=prompt,
            route="imagine",
            cost_usd=cost_usd,
            model=model,
            latency_sec=ctx.elapsed,
            caller=caller,
        )

        return MediaResult(
            response=",".join([img.url for img in images]),
            text=summary_text,
            summary=summary_text,
            finish_reason="final_answer",
            cost_usd=cost_usd,
            model=model,
            tokens=0,
            latency_sec=ctx.elapsed,
            route="imagine",
            plane="API",
            images=[img.url for img in images],
            imagine_params={"prompt": prompt, "n": n, "aspect_ratio": aspect_ratio, "resolution": resolution},
        )


async def generate_video(
    prompt: str,
    model: str = "grok-imagine-video",
    image_path: Optional[str] = None,
    image_url: Optional[str] = None,
    video_path: Optional[str] = None,
    video_url: Optional[str] = None,
    reference_image_paths: Optional[List[str]] = None,
    reference_image_urls: Optional[List[str]] = None,
    duration: Optional[int] = None,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None
) -> MediaResult:
    """Generate or edit videos with Grok Imagine.

    Args:
        prompt: Video description, or the edit instruction for video editing.
        model: Video model (default `grok-imagine-video`).
        image_path: Local image to use as the starting frame.
        image_url: Public image URL to use as the starting frame.
        video_path: Local video to edit (max 20 MB, .mp4, ≤ 8.7s).
        video_url: Public video URL to edit (.mp4, ≤ 8.7s).
        reference_image_paths: Local images used as style/subject references.
        reference_image_urls: Public image URLs used as style/subject references.
        duration: Video length in seconds (1–15, ignored when editing).
        aspect_ratio: Aspect ratio like `"16:9"` or `"9:16"`.
        resolution: `"480p"` or `"720p"`.
    """
    caller = await _enforce_media_budget()
    async with GrokInvocationContext(model, logger, append_signature=True) as ctx:
        if duration is not None and not 1 <= int(duration) <= 15:
            raise ValueError("duration must be between 1 and 15 seconds")
        params = {
            "model": model,
            "prompt": prompt
        }
        
        if duration:
            params["duration"] = duration
        if aspect_ratio:
            params["aspect_ratio"] = aspect_ratio
        if resolution:
            params["resolution"] = resolution

        def _call_video():
            if image_path:
                resolved_img = PathResolver.validate_path(image_path)
                validate_local_input(
                    resolved_img,
                    max_bytes=input_limit("UNIGROK_MAX_MEDIA_INPUT_BYTES", 20_000_000, 1_024, 100_000_000),
                    allowed_suffixes=(".jpg", ".jpeg", ".png", ".webp"),
                    label="image",
                )
                base64_string = encode_image_to_base64(str(resolved_img), max_bytes=input_limit("UNIGROK_MAX_MEDIA_INPUT_BYTES", 20_000_000, 1_024, 100_000_000))
                ext = resolved_img.suffix.lower().replace('.', '')
                params["image_url"] = f"data:image/{ext};base64,{base64_string}"
            elif image_url:
                params["image_url"] = image_url
            
            if video_path:
                resolved_vid = PathResolver.validate_path(video_path)
                validate_local_input(
                    resolved_vid,
                    max_bytes=input_limit("UNIGROK_MAX_MEDIA_INPUT_BYTES", 20_000_000, 1_024, 100_000_000),
                    allowed_suffixes=(".mp4",),
                    label="video",
                )
                base64_string = encode_video_to_base64(str(resolved_vid), max_bytes=input_limit("UNIGROK_MAX_MEDIA_INPUT_BYTES", 20_000_000, 1_024, 100_000_000))
                ext = resolved_vid.suffix.lower().replace('.', '')
                params["video_url"] = f"data:video/{ext};base64,{base64_string}"
            elif video_url:
                params["video_url"] = video_url

            refs = []
            if reference_image_paths:
                for path in reference_image_paths:
                    resolved_ref = PathResolver.validate_path(path)
                    validate_local_input(
                        resolved_ref,
                        max_bytes=input_limit("UNIGROK_MAX_MEDIA_INPUT_BYTES", 20_000_000, 1_024, 100_000_000),
                        allowed_suffixes=(".jpg", ".jpeg", ".png", ".webp"),
                        label="reference image",
                    )
                    base64_string = encode_image_to_base64(str(resolved_ref), max_bytes=input_limit("UNIGROK_MAX_MEDIA_INPUT_BYTES", 20_000_000, 1_024, 100_000_000))
                    ext = resolved_ref.suffix.lower().replace('.', '')
                    refs.append(f"data:image/{ext};base64,{base64_string}")
            if reference_image_urls:
                refs.extend(reference_image_urls)
            if refs:
                params["reference_image_urls"] = refs

            client = get_xai_client()
            res = client.video.generate(**params)
            return res

        response = await run_blocking(_call_video, timeout=120.0)
        output_text = f"## Generated Video\n\n\n**URL:** {response.url}\n\n\n**Duration:** {response.duration}s\n\n"
        cost_usd = float(getattr(response, "cost_usd", 0.0) or 0.0)
        summary_text = ctx.format_output(output_text, [response])
        await _record_media_telemetry(
            intent=prompt,
            route="imagine_video",
            cost_usd=cost_usd,
            model=model,
            latency_sec=ctx.elapsed,
            caller=caller,
        )

        return MediaResult(
            response=output_text,
            text=summary_text,
            summary=summary_text,
            finish_reason="final_answer",
            cost_usd=cost_usd,
            model=model,
            tokens=0,
            latency_sec=ctx.elapsed,
            route="imagine_video",
            plane="API",
            video_url=response.url,
            duration_sec=float(response.duration) if response.duration else None,
            imagine_params={
                "prompt": prompt,
                "duration": duration,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "has_image_init": bool(image_path or image_url),
                "has_video_init": bool(video_path or video_url),
            },
        )


async def extend_video(
    prompt: str,
    video_url: str,
    model: str = "grok-imagine-video",
    duration: Optional[int] = None,
) -> MediaResult:
    """Extend an existing video with a follow-up prompt.

    Args:
        prompt: What should happen in the extended segment.
        video_url: Public URL of the source video (.mp4, 2–15 s).
        model: Video model (default `grok-imagine-video`).
        duration: Length of the extension in seconds (2–10, default 6).
    """
    caller = await _enforce_media_budget()
    async with GrokInvocationContext(model, logger, append_signature=True) as ctx:
        if duration is not None and not 2 <= int(duration) <= 10:
            raise ValueError("duration must be between 2 and 10 seconds")
        params = {"model": model, "prompt": prompt, "video_url": video_url}
        if duration:
            params["duration"] = duration

        def _extend():
            client = get_xai_client()
            res = client.video.extend(**params)
            return res

        response = await run_blocking(_extend, timeout=120.0)
        output_text = f"## Extended Video\n\n\n**URL:** {response.url}\n\n\n**Duration:** {response.duration}s\n\n"
        cost_usd = float(getattr(response, "cost_usd", 0.0) or 0.0)
        summary_text = ctx.format_output(output_text, [response])
        await _record_media_telemetry(
            intent=prompt,
            route="extend_video",
            cost_usd=cost_usd,
            model=model,
            latency_sec=ctx.elapsed,
            caller=caller,
        )

        return MediaResult(
            response=output_text,
            text=summary_text,
            summary=summary_text,
            finish_reason="final_answer",
            cost_usd=cost_usd,
            model=model,
            tokens=0,
            latency_sec=ctx.elapsed,
            route="extend_video",
            plane="API",
            video_url=response.url,
            duration_sec=float(response.duration) if response.duration else None,
            imagine_params={"prompt": prompt, "video_url": video_url, "duration": duration},
        )


def register_media_tools(mcp: FastMCP):
    # grok_imagine was deleted — it was a strict subset of generate_image.
    mcp.add_tool(generate_image)
    mcp.add_tool(generate_video)
    mcp.add_tool(extend_video)

register_internal_tool("generate_image", generate_image)
register_internal_tool("generate_video", generate_video)
