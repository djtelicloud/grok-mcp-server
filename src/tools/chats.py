# src/tools/chats.py
# Decomposed Chat and Reasoning tools for UniGrok MCP

import logging
import os
from typing import Any, Dict, Optional, List, Literal
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from ..models.results import ChatResult, AgentResult, ReflectionResult
from ..identity import caller_from_mcp_context, scoped_session

from ..utils import (
    store,
    load_history,
    append_and_save_history,
    MetaLayer,
    GrokInvocationContext,
    get_dynamic_context,
    get_xai_client,
    encode_image_to_base64,
    register_internal_tool,
    PathResolver,
    _parse_structured,
    run_agent_turn,
    run_blocking,
    input_limit,
    validate_local_input,
    DEFAULT_PLANNING_MODEL,
)
from xai_sdk.chat import system, user, assistant, image, file as xai_file

logger = logging.getLogger("GrokMCP")

def _model_to_mode(model: str) -> Literal["auto", "reasoning", "composer"]:
    if "composer" in model:
        return "composer"
    if "reasoning" in model:
        return "reasoning"
    return "auto"


def _validate_agent_count(model: str, agent_count: Optional[int]) -> Optional[str]:
    if agent_count is None:
        return None
    if agent_count not in (4, 16):
        return "Input Validation Error: agent_count must be either 4 or 16."
    if not model.startswith("grok-4.20-multi-agent"):
        return "Input Validation Error: agent_count is only supported with a grok-4.20-multi-agent model."
    return None


def _research_agent_count() -> int:
    """Multi-agent fan-out for the agent tool's research mode.

    UNIGROK_RESEARCH_AGENT_COUNT — the SDK only accepts 4 or 16, so anything
    else (unset, unparsable, out of range) falls back to 4."""
    raw = os.environ.get("UNIGROK_RESEARCH_AGENT_COUNT", "4").strip()
    try:
        value = int(raw)
    except ValueError:
        return 4
    return value if value in (4, 16) else 4

# Pydantic Schemas for RPC Boundary Validation
class GrokAgentInput(BaseModel):
    prompt: str = Field(..., description="The high-signal goal or task for the Grok Agent loop.")
    max_iterations: int = Field(5, ge=1, le=10, description="Strict cap on reviewer-driven correction retries.")
    cost_limit: float = Field(0.50, gt=0.0, le=2.00, description="Total budget in USD before hard abort.")


class GrokReflectionResult(BaseModel):
    """Schema for a focused, tool-free Grok critique."""

    verdict: Literal["pass", "needs_changes", "fail"] = Field(
        ...,
        description="Overall judgment of the reviewed artifact.",
    )
    summary: str = Field(..., description="One concise paragraph with the core judgment.")
    strengths: List[str] = Field(default_factory=list, description="Specific strengths worth preserving.")
    issues: List[str] = Field(default_factory=list, description="Concrete problems or missing evidence.")
    recommendations: List[str] = Field(default_factory=list, description="Actionable improvements.")
    next_action: str = Field("", description="The single highest-value next action.")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Confidence in this verdict from 0 to 1.")


def _agent_progress_reporter(ctx: Context):
    """Adapt agent progress events onto MCP progress notifications.

    Depth events carry n-of-max progress; tool events reuse the last depth as
    the progress value with a descriptive message. content_delta events are
    skipped (per-token notifications would flood the transport). Reporting is
    best-effort: a failed notification never breaks the run (ctx.report_progress
    is already a no-op when the client sent no progressToken).
    """
    state = {"depth": 0.0, "total": None}

    async def _on_event(event: Dict[str, Any]) -> None:
        try:
            kind = event.get("type")
            if kind == "depth":
                state["depth"] = float(event.get("depth") or 0)
                state["total"] = float(event.get("max_depth") or 0) or None
                message = (
                    f"agent depth {event.get('depth')}/{event.get('max_depth')} "
                    f"(cost ${float(event.get('cost_usd') or 0.0):.4f})"
                )
            elif kind == "tool_start":
                message = f"tool {event.get('tool')} started"
            elif kind == "tool_end":
                status = "ok" if event.get("success") else "error"
                message = f"tool {event.get('tool')} {status} ({float(event.get('elapsed') or 0.0):.1f}s)"
            else:
                return
            await ctx.report_progress(state["depth"], state["total"], message)
        except Exception as exc:
            logger.debug(f"MCP progress notification failed: {exc}")

    return _on_event


async def agent(
    task: str,
    session: Optional[str] = None,
    mode: Literal["auto", "fast", "reasoning", "thinking", "research"] = "auto",
    model: Optional[str] = None,
    require_reasoning_level: Optional[Literal["low", "medium", "high"]] = None,
    plane: Literal["auto", "cli", "api"] = "auto",
    fallback_policy: Literal["same_plane", "cross_plane"] = "cross_plane",
    ctx: Optional[Context] = None,
) -> AgentResult:
    """Run the unified UniGrok agent on any task. This is the headline entry
    point — use it by default for anything nontrivial instead of picking a
    specialized tool.

    It auto-routes across Grok models (planning model for reasoning-heavy
    tasks, coding model otherwise), gives the model its full action space on
    every request — xAI server-side web search, X search, and sandboxed code
    execution plus local file, git, and test tools — and lets the model decide
    for itself whether to act. Pass a session name and it remembers prior
    turns, including tool observations, so multi-step work continues across
    calls. When the client requests progress (MCP progressToken), depth and
    tool progress is reported live via the injected FastMCP context.

    Args:
        task: The goal, question, or task for the agent.
        session: Optional session name. Persists conversation history and tool
            traces so later calls can continue the work.
        mode: `"auto"` (default) self-routes; `"fast"` forces a single toolless
            completion for trivial prompts; `"reasoning"` pins the planning
            model; `"thinking"` runs the agent loop plus a schema-enforced
            reflection review for the hardest tasks (slowest, most expensive);
            `"research"` uses the catalog's multi-agent-capable research model
            (agent_count from UNIGROK_RESEARCH_AGENT_COUNT, 4 or 16) with
            inline citations requested — sources come back under `citations`.
        model: Optional Grok model id. Leave unset to let routing choose.
        require_reasoning_level: Minimum required Grok reasoning level (low, medium, high).
        plane: Starting credential plane. `auto` follows server policy; `cli`
            starts on the SuperGrok subscription; `api` starts on the metered
            developer API.
        fallback_policy: `same_plane` forbids crossing the billing boundary;
            `cross_plane` permits bounded recovery on the other xAI plane.

    Returns:
        AgentResult containing execution metadata and responses.
    """
    session = scoped_session(session)
    is_research = mode == "research"
    layer: MetaLayer = await run_agent_turn(
        prompt=task,
        session=session,
        model=model,
        mode=mode if mode in ("reasoning", "research") else "auto",
        thinking_mode=(mode == "thinking"),
        enable_agentic=(mode != "fast"),
        agent_count=_research_agent_count() if is_research else None,
        include=["inline_citations"] if is_research else None,
        on_event=_agent_progress_reporter(ctx) if ctx is not None else None,
        # Which agent is calling: the clientInfo name from MCP initialize
        # (None for clients that never sent it) — attributed to telemetry,
        # session metadata, and per-caller budgets downstream.
        caller=caller_from_mcp_context(ctx) if ctx is not None else None,
        require_reasoning_level=require_reasoning_level,
        plane=plane,
        fallback_policy=fallback_policy,
    )
    citations_mapped = [{"url": url} for url in layer.citations] if layer.citations else None
    return AgentResult(
        response=layer.generation,
        text=layer.generation,
        finish_reason=layer.finish_reason if layer.finish_reason in ["final_answer", "fallback", "tool_calls", "length", "unknown", "error"] else "unknown",
        cost_usd=layer.cost_usd,
        model=layer.model or "unknown",
        profile=layer.profile,
        tokens=layer.tokens,
        latency_sec=layer.latency,
        route=layer.route or "unknown",
        plane=layer.plane if layer.plane in ["API", "CLI", "CLI-Fallback", "local", "utility"] else "API",
        why=layer.routing_why or "auto",
        routing=layer.routing_receipt or None,
        credentials=getattr(layer, "credentials", None) or None,
        degraded=layer.degraded,
        citations=citations_mapped,
        requested_plane=plane,
        resolved_plane=(layer.routing_receipt or {}).get("resolved_plane"),
        fallback_policy=fallback_policy,
        billing_class=(layer.routing_receipt or {}).get("billing_class"),
    )


async def chat(
    prompt: str,
    session: Optional[str] = None,
    model: str = "grok-build-0.1",
    system_prompt: Optional[str] = None,
    agent_count: Optional[int] = None,
    enable_agentic: bool = True,
    require_reasoning_level: Optional[Literal["low", "medium", "high"]] = None,
) -> ChatResult:
    """Send a text prompt to a Grok model and return its reply.

    Absorbs the old `agentic_chat` tool: the ReAct AgentLoop is now the
    default route, so the model has its tool surface and self-directs.
    Set `enable_agentic=False` to force a single toolless completion.

    Args:
        prompt: User message to send to the model.
        session: Optional session name. Persists conversation history.
        model: Grok model id (defaults to `grok-build-0.1`).
        system_prompt: Optional system instruction prepended to the conversation.
        agent_count: 4 or 16. Only valid with `grok-4.20-multi-agent`.
        enable_agentic: If True (default), runs through the ReAct AgentLoop.
        require_reasoning_level: Minimum required Grok reasoning level (low, medium, high).
    """
    session = scoped_session(session)
    validation_error = _validate_agent_count(model, agent_count)
    if validation_error:
        return ChatResult(
            response=validation_error,
            text=validation_error,
            finish_reason="error",
            cost_usd=0.0,
            model=model,
            route="unknown",
            plane="API",
        )

    mode = _model_to_mode(model)

    dynamic_sys_prompt, ctx_injected, context_id = await get_dynamic_context(prompt=prompt)
    if system_prompt:
        dynamic_sys_prompt += f"\nAdditional Instructions:\n{system_prompt}"

    import src.server
    layer: MetaLayer = await src.server.orchestrate(
        prompt=prompt,
        session=session,
        mode=mode,
        thinking_mode=False,
        store=store,
        dynamic_sys_prompt=dynamic_sys_prompt,
        requested_model=model,
        enable_agentic=enable_agentic,
        context_id=context_id,
        agent_count=agent_count,
        require_reasoning_level=require_reasoning_level,
    )

    if session and layer.generation:
        history = await load_history(session, store)
        await append_and_save_history(
            session,
            history,
            prompt,
            layer.generation,
            store,
            metadata={"model": model, "plane": layer.plane, "context_id": context_id, "tokens": layer.tokens, "cost": layer.cost_usd},
        )
        # Server-state runs persist the real stored-completion id so the next
        # turn continues the thread; every other turn (CLI plane, toolless
        # fast path) RESETS the thread head to the legacy session placeholder
        # — never sent upstream — so a later server-state turn replays the
        # full local history instead of continuing a stale upstream thread
        # that never saw this exchange.
        await store.save_session(session, api_thread_id=layer.response_id or session, model=model)

    is_cli = "cli" in layer.plane.lower()
    async with GrokInvocationContext(model=model, logger=logger, is_cli=is_cli, append_signature=True) as ctx:
        ctx.context_injected = ctx_injected
        ctx.fallback_occurred = layer.fallback_occurred
        ctx.elapsed = layer.latency
        ctx.finish_reason = layer.finish_reason
        
        output_content = layer.generation
        if layer.reasoning and mode == "reasoning":
            output_content = (
                f"<thinking>\n"
                f"### Plan\n{layer.plan}\n\n"
                f"### Strategic Reasoning\n{layer.reasoning}\n\n"
                f"### Verification/Reflection\n{layer.reflection}\n"
                f"</thinking>\n\n"
                f"{layer.generation}"
            )
        
        formatted_text = ctx.format_output(output_content)
        citations_mapped = [{"url": url} for url in layer.citations] if layer.citations else None
        return ChatResult(
            response=output_content,
            text=formatted_text,
            finish_reason=layer.finish_reason if layer.finish_reason in ["final_answer", "fallback", "tool_calls", "length", "unknown", "error"] else "unknown",
            cost_usd=layer.cost_usd,
            model=model,
            profile=layer.profile,
            tokens=layer.tokens,
            latency_sec=layer.latency,
            route=layer.route or "unknown",
            plane=layer.plane if layer.plane in ["API", "CLI", "CLI-Fallback", "local", "utility"] else "API",
            response_id=layer.response_id,
            session=session,
            citations=citations_mapped,
        )


async def grok_agent(
    prompt: str,
    session: Optional[str] = None,
    model: str = DEFAULT_PLANNING_MODEL,
    system_prompt: Optional[str] = None,
    max_iterations: int = 5,
    cost_limit: float = 0.50,
) -> AgentResult:
    """Unified @grok Entry Point: run the thinking route — the ReAct AgentLoop
    wrapped in a schema-enforced reflection loop — with explicit retry and
    budget caps.

    Args:
        prompt: Task or question for the agent.
        session: Optional session name for persistent history in chats.
        model: Grok model id (default `grok-4.5`).
        system_prompt: Optional system instruction prepended to the conversation.
        max_iterations: Strict cap on reviewer-driven correction retries (default 5).
        cost_limit: Total budget in USD before hard abort (default 0.50).
    """
    session = scoped_session(session)
    try:
        inputs = GrokAgentInput(prompt=prompt, max_iterations=max_iterations, cost_limit=cost_limit)
    except Exception as e:
        return AgentResult(
            response=f"Input Validation Error: {str(e)}",
            text=f"Input Validation Error: {str(e)}",
            finish_reason="error",
            cost_usd=0.0,
            model=model,
            route="unknown",
            plane="API",
        )

    dynamic_sys_prompt, ctx_injected, context_id = await get_dynamic_context(prompt=prompt)
    if system_prompt:
        dynamic_sys_prompt += f"\nAdditional Instructions:\n{system_prompt}"

    from ..utils import run_thinking_loop

    layer = await run_thinking_loop(
        inputs.prompt,
        session=session,
        store=store,
        dynamic_sys_prompt=dynamic_sys_prompt,
        model=model,
        context_id=context_id,
        max_reflections=inputs.max_iterations,
        global_budget_usd=inputs.cost_limit,
    )

    if session and layer.generation:
        history = await load_history(session, store)
        await append_and_save_history(
            session,
            history,
            inputs.prompt,
            layer.generation,
            store,
            metadata={"model": model, "plane": layer.plane, "context_id": context_id, "tokens": layer.tokens, "cost": layer.cost_usd},
        )
        # api_thread_id only updates when the run produced a stored-completion
        # id (None leaves the existing value untouched).
        await store.save_session(session, api_thread_id=layer.response_id or None, model=model)

    async with GrokInvocationContext(model=model, logger=logger, is_cli=False, append_signature=True) as ctx:
        ctx.context_injected = ctx_injected
        ctx.elapsed = layer.latency
        ctx.finish_reason = layer.finish_reason
        
        output_content = layer.generation
        if layer.plan:
            output_content = (
                f"<thinking>\n"
                f"### Plan\n{layer.plan}\n\n"
                f"### Strategic Reasoning\n{layer.reasoning}\n\n"
                f"### Verification/Reflection\n{layer.reflection}\n"
                f"</thinking>\n\n"
                f"{layer.generation}"
            )
        
        formatted_text = ctx.format_output(output_content)
        citations_mapped = [{"url": url} for url in layer.citations] if layer.citations else None
        return AgentResult(
            response=output_content,
            text=formatted_text,
            finish_reason=layer.finish_reason if layer.finish_reason in ["final_answer", "fallback", "tool_calls", "length", "unknown", "error"] else "unknown",
            cost_usd=layer.cost_usd,
            model=model,
            profile=layer.profile,
            tokens=layer.tokens,
            latency_sec=layer.latency,
            route=layer.route or "unknown",
            plane=layer.plane if layer.plane in ["API", "CLI", "CLI-Fallback", "local", "utility"] else "API",
            why=layer.routing_why or "auto",
            routing=layer.routing_receipt or None,
            degraded=layer.degraded,
            citations=citations_mapped,
        )


async def grok_reflect(
    subject: str,
    criteria: Optional[str] = None,
    context: Optional[str] = None,
    model: str = DEFAULT_PLANNING_MODEL,
) -> ReflectionResult:
    """Run a structured, tool-free Grok review of an artifact or plan.

    Use this when a client needs a deterministic critique shape rather than a
    full agent run. It calls xAI structured outputs through the shared
    `_parse_structured` helper, so the reflection pass cannot invoke local
    tools and degrades explicitly if structured parsing is unavailable.
    """
    if not subject or not subject.strip():
        return ReflectionResult(
            ok=False,
            critique={},
            response="Input Validation Error: subject must not be blank.",
            text="Input Validation Error: subject must not be blank.",
            finish_reason="error",
            cost_usd=0.0,
            model=model,
            route="unknown",
            plane="API",
        )

    system_prompt = (
        "You are UniGrok's Reflection Oracle. Review the submitted artifact "
        "with senior engineering judgment. Be concrete, evidence-aware, and "
        "avoid vague praise. Return only the requested structured schema."
    )
    user_prompt = (
        f"Artifact to review:\n{subject.strip()}\n\n"
        f"Review criteria:\n{(criteria or 'Correctness, completeness, risk, maintainability, and verification.').strip()}\n\n"
        f"Additional context:\n{(context or 'No additional context provided.').strip()}"
    )

    parsed, tokens, cost = await _parse_structured(
        GrokReflectionResult,
        system_prompt,
        user_prompt,
        model,
        timeout=60.0,
        logger=logger,
    )
    if parsed is None:
        return ReflectionResult(
            ok=False,
            critique={},
            response="structured_reflection_unavailable",
            text="structured_reflection_unavailable",
            finish_reason="error",
            cost_usd=cost,
            model=model,
            tokens=tokens,
            route="unknown",
            plane="API",
        )

    return ReflectionResult(
        ok=True,
        critique=parsed.model_dump(),
        response=str(parsed.model_dump()),
        text=str(parsed.model_dump()),
        finish_reason="final_answer",
        cost_usd=cost,
        model=model,
        tokens=tokens,
        route="unknown",
        plane="API",
    )


async def stateful_chat(
    prompt: str,
    model: str = DEFAULT_PLANNING_MODEL,
    response_id: Optional[str] = None,
    system_prompt: Optional[str] = None
) -> ChatResult:
    """Continue a server-side stored conversation using xAI's stateful chat.

    Args:
        prompt: User message to append.
        model: Grok model id (default `grok-4.5`).
        response_id: ID of the previous response to continue from.
        system_prompt: Optional system instruction.

    Returns:
        ChatResult containing execution metadata and responses.
    """
    async with GrokInvocationContext(model, logger, append_signature=True) as ctx:
        chat_params = {"model": model, "store_messages": True}
        if response_id:
            chat_params["previous_response_id"] = response_id

        def _call_stateful():
            client = get_xai_client()
            chat = client.chat.create(**chat_params)
            if system_prompt and not response_id:
                chat.append(system(system_prompt))
            chat.append(user(prompt))
            res = chat.sample()
            return res

        response = await run_blocking(_call_stateful, timeout=60.0)
        cost_usd = float(getattr(response, "cost_usd", 0.0) or 0.0)
        finish_reason = getattr(response, "finish_reason", "final_answer") or "final_answer"
        tokens_val = 0
        if hasattr(response, 'usage') and response.usage:
            tokens_val = getattr(response.usage, 'prompt_tokens', 0) + getattr(response.usage, 'completion_tokens', 0)
        
        citations_mapped = [{"url": url} for url in response.citations] if hasattr(response, 'citations') and response.citations else None
        
        return ChatResult(
            response=response.content,
            text=ctx.format_output(response.content, [response]),
            finish_reason=finish_reason if finish_reason in ["final_answer", "fallback", "tool_calls", "length", "unknown", "error"] else "unknown",
            cost_usd=cost_usd,
            model=model,
            tokens=tokens_val,
            latency_sec=ctx.elapsed,
            route="stateful",
            plane="API",
            response_id=response.id,
            citations=citations_mapped,
        )


async def retrieve_stateful_response(response_id: str) -> str:
    """Fetch a stored chat completion from xAI by its response ID.

    Args:
        response_id: ID returned by a prior `stateful_chat` call.
    """
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        def _get_stored():
            client = get_xai_client()
            res = client.chat.get_stored_completion(response_id)
            return res
            
        responses = await run_blocking(_get_stored, timeout=30.0)
        if not responses:
            return f"No response found for id {response_id}"
        response = responses[0] if isinstance(responses, list) else responses
        return ctx.format_output(f"{response.content}\n\n**Response ID:** `{response.id}`")


async def delete_stateful_response(response_id: str) -> str:
    """Delete a stored chat completion from xAI's servers.

    Args:
        response_id: ID of the stored response to remove.
    """
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        def _delete_stored():
            client = get_xai_client()
            client.chat.delete_stored_completion(response_id)
            
        await run_blocking(_delete_stored, timeout=30.0)
        return ctx.format_output(f"Deleted stored response `{response_id}` successfully.")


async def chat_with_vision(
    prompt: str,
    session: Optional[str] = None,
    model: str = DEFAULT_PLANNING_MODEL,
    image_paths: Optional[List[str]] = None,
    image_urls: Optional[List[str]] = None,
    detail: str = "auto"
) -> ChatResult:
    """Analyze one or more images with a Grok vision model.

    Args:
        prompt: Question or instruction about the image(s).
        session: Optional session name for persistent history in chats.
        model: Vision-capable Grok model (default `grok-4.5`).
        image_paths: Local image file paths to analyze.
        image_urls: Public image URLs to analyze.
        detail: Image detail level. One of `"auto"`, `"low"`, or `"high"`.
    """
    session = scoped_session(session)
    async with GrokInvocationContext(model, logger, append_signature=True) as ctx:
        history = (await load_history(session, store)) if session else []

        def _call_vision():
            client = get_xai_client()
            chat = client.chat.create(model=model, store_messages=False)

            for message in history:
                if message["role"] == "user":
                    chat.append(user(message["content"]))
                elif message["role"] == "assistant":
                    chat.append(assistant(message["content"]))

            user_content = []
            if image_paths:
                for path in image_paths:
                    resolved_path = PathResolver.validate_path(path)
                    ext = resolved_path.suffix.lower().replace('.', '')
                    if ext not in ["jpg", "jpeg", "png"]:
                        raise ValueError(f"Unsupported image type: {ext}")
                    validate_local_input(
                        resolved_path,
                        max_bytes=input_limit("UNIGROK_MAX_MEDIA_INPUT_BYTES", 20_000_000, 1_024, 100_000_000),
                        allowed_suffixes=(".jpg", ".jpeg", ".png"),
                        label="image",
                    )
                    base64_img = encode_image_to_base64(
                        str(resolved_path),
                        max_bytes=input_limit("UNIGROK_MAX_MEDIA_INPUT_BYTES", 20_000_000, 1_024, 100_000_000),
                    )
                    user_content.append(image(image_url=f"data:image/{ext};base64,{base64_img}", detail=detail))

            if image_urls:
                for url in image_urls:
                    user_content.append(image(image_url=url, detail=detail))

            user_content.append(prompt)
            chat.append(user(*user_content))
            res = chat.sample()
            return res

        response = await run_blocking(_call_vision, timeout=60.0)

        if session:
            await append_and_save_history(session, history, prompt, response.content, store)

        cost_usd = float(getattr(response, "cost_usd", 0.0) or 0.0)
        finish_reason = getattr(response, "finish_reason", "final_answer") or "final_answer"
        ctx.finish_reason = finish_reason

        formatted_text = ctx.format_output(response.content, [response])
        
        tokens_val = 0
        if hasattr(response, 'usage') and response.usage:
            tokens_val = getattr(response.usage, 'prompt_tokens', 0) + getattr(response.usage, 'completion_tokens', 0)

        citations_mapped = [{"url": url} for url in response.citations] if hasattr(response, 'citations') and response.citations else None

        return ChatResult(
            response=response.content,
            text=formatted_text,
            finish_reason=finish_reason if finish_reason in ["final_answer", "fallback", "tool_calls", "length", "unknown", "error"] else "unknown",
            cost_usd=cost_usd,
            model=model,
            tokens=tokens_val,
            latency_sec=ctx.elapsed,
            route="vision",
            plane="API",
            session=session,
            citations=citations_mapped,
        )


async def chat_with_files(
    prompt: str,
    file_ids: List[str],
    session: Optional[str] = None,
    model: str = DEFAULT_PLANNING_MODEL,
    system_prompt: Optional[str] = None,
) -> ChatResult:
    """Chat with Grok using one or more previously uploaded files as context.

    Args:
        prompt: Question or instruction about the attached files.
        file_ids: IDs returned by `xai_upload_file`.
        session: Optional session name for persistent local history.
        model: Grok model id (default `grok-4.5`).
        system_prompt: Optional system instruction prepended to the conversation.
    """
    if not file_ids:
        error_msg = "Input Validation Error: file_ids must contain at least one uploaded file ID."
        return ChatResult(
            response=error_msg,
            text=error_msg,
            finish_reason="error",
            cost_usd=0.0,
            model=model,
            route="unknown",
            plane="API",
        )

    dynamic_sys_prompt, ctx_injected, context_id = await get_dynamic_context(prompt=prompt)
    if system_prompt:
        dynamic_sys_prompt += f"\nAdditional Instructions:\n{system_prompt}"

    session = scoped_session(session)
    async with GrokInvocationContext(model, logger, append_signature=True) as ctx:
        ctx.context_injected = ctx_injected
        history = (await load_history(session, store)) if session else []

        def _call_files():
            client = get_xai_client()
            chat = client.chat.create(model=model)
            chat.append(system(dynamic_sys_prompt))

            for message in history:
                if message["role"] == "user":
                    chat.append(user(message["content"]))
                elif message["role"] == "assistant":
                    chat.append(assistant(message["content"]))

            attachments = [xai_file(fid) for fid in file_ids]
            chat.append(user(prompt, *attachments))
            res = chat.sample()
            return res

        response = await run_blocking(_call_files, timeout=60.0)

        if session:
            await append_and_save_history(
                session,
                history,
                prompt,
                response.content,
                store,
                metadata={"model": model, "plane": "API", "context_id": context_id},
            )

        result = [response.content]
        if response.citations:
            result.append("\n\n**Sources:**")
            for url in response.citations:
                result.append(f"- {url}")
        
        cost_usd = float(getattr(response, "cost_usd", 0.0) or 0.0)
        finish_reason = getattr(response, "finish_reason", "final_answer") or "final_answer"
        ctx.finish_reason = finish_reason

        formatted_text = ctx.format_output("\n".join(result), [response])
        
        tokens_val = 0
        if hasattr(response, 'usage') and response.usage:
            tokens_val = getattr(response.usage, 'prompt_tokens', 0) + getattr(response.usage, 'completion_tokens', 0)

        citations_mapped = [{"url": url} for url in response.citations] if hasattr(response, 'citations') and response.citations else None

        return ChatResult(
            response=response.content,
            text=formatted_text,
            finish_reason=finish_reason if finish_reason in ["final_answer", "fallback", "tool_calls", "length", "unknown", "error"] else "unknown",
            cost_usd=cost_usd,
            model=model,
            tokens=tokens_val,
            latency_sec=ctx.elapsed,
            route="files",
            plane="API",
            session=session,
            citations=citations_mapped,
        )


def register_chat_tools(mcp: FastMCP):
    mcp.add_tool(agent)
    mcp.add_tool(chat)
    mcp.add_tool(grok_agent)
    mcp.add_tool(grok_reflect, annotations=ToolAnnotations(readOnlyHint=True))
    mcp.add_tool(stateful_chat)
    mcp.add_tool(retrieve_stateful_response, annotations=ToolAnnotations(readOnlyHint=True))
    mcp.add_tool(delete_stateful_response, annotations=ToolAnnotations(destructiveHint=True))
    mcp.add_tool(chat_with_vision)
    mcp.add_tool(chat_with_files)


async def raw_get_session_history(session: str) -> str:
    """Get local chat history for a session — lets agent recall prior context."""
    session = scoped_session(session)
    history = await load_history(session, store)
    if not history:
        return f"No history found for session '{session}'."
    lines = []
    for msg in history[-20:]:  # Last 20 messages
        lines.append(f"{msg['role']}: {msg['content']}")
    return "\n".join(lines)

register_internal_tool("get_session_history", raw_get_session_history)
