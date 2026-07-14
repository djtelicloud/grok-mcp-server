---
okf_version: "0.1"
title: "Media Generation"
type: "topic"
description: "How to generate and edit images and videos using Grok Imagine, with schema inputs and results contracts."
---

# Media Generation

UniGrok's full trusted stdio server integrates xAI's image and video generation
capabilities.

> **Surface scope:** `generate_image`, `generate_video`, and `extend_video` are
> API-plane tools on trusted stdio only. They are not exposed by stable HTTP at
> `:4765/mcp`, and contributor Forge does not add them. A caller must confirm
> live `tools/list` before relying on these names. Local file inputs resolve
> only within the trusted stdio workspace; URL inputs do not grant filesystem
> access. In ordinary local trusted stdio, the resolved workspace
> defaults to the UniGrok service root unless `WORKSPACE_ROOT` is set; it never
> follows the calling IDE project implicitly.

## Media Tools Schema Contract

All media tools return `MediaResult` (inheriting from `BaseResult`) to guarantee structured access to URLs and duration metadata.

### `MediaResult` Output Schema
```json
{
  "response": "https://img.x.ai/image-xyz.png",
  "text": "Human-formatted summary markdown...",
  "finish_reason": "final_answer",
  "cost_usd": 0.05,
  "model": "grok-imagine-image",
  "profile": null,
  "tokens": 0,
  "latency_sec": 8.5,
  "route": "imagine",
  "plane": "API",
  "images": ["https://img.x.ai/image-xyz.png"],
  "video_url": null,
  "duration_sec": null,
  "imagine_params": {
    "prompt": "A sunset over the mountains",
    "n": 1,
    "aspect_ratio": "16:9",
    "resolution": "1k"
  }
}
```

## Trusted stdio media tools

### 1. `generate_image`
Generates new images or edits existing ones using text instructions.
- **Parameters**:
  - `prompt` (string, required): Image generation description.
  - `model` (string, optional): Default `"grok-imagine-image"`.
  - `image_paths` (array of strings, optional): Paths to local files to edit.
  - `image_urls` (array of strings, optional): Public URLs.
  - `n` (integer, optional): Number of images (1-10, default 1).
  - `image_format` (string, optional): `"url"` or `"base64"`.
  - `aspect_ratio` (string, optional): e.g., `"16:9"`, `"1:1"`, `"9:16"`.
  - `resolution` (string, optional): `"1k"` or `"2k"`.

### 2. `generate_video`
Generates short video clips from text, images, or source video segments.
- **Parameters**:
  - `prompt` (string, required): Video generation description.
  - `model` (string, optional): Default `"grok-imagine-video"`.
  - `image_path` / `image_url` (string, optional): Source starting frame image.
  - `video_path` / `video_url` (string, optional): Video file for editing.
  - `duration` (integer, optional): Duration in seconds (1-15).
  - `aspect_ratio` (string, optional): e.g. `"16:9"`, `"9:16"`.
  - `resolution` (string, optional): `"480p"` or `"720p"`.

### 3. `extend_video`
Appends a follow-up segment to an existing video.
- **Parameters**:
  - `prompt` (string, required): What should happen in the next segment.
  - `video_url` (string, required): Public URL of the source video.
  - `model` (string, optional): Default `"grok-imagine-video"`.
  - `duration` (integer, optional): Length of extension in seconds (2-10).
