from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any, Literal

class BaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")  # Strict — no silent extra fields

    response: str = Field(..., description="Raw model output or primary content.")
    text: Optional[str] = Field(None, description="Human-formatted output (includes footers, citations, cost summary).")
    finish_reason: Literal["final_answer", "fallback", "tool_calls", "length", "unknown", "error"] = Field("unknown")
    cost_usd: float = Field(0.0, description="Exact USD cost from xAI billing metadata.")
    model: str = Field(..., description="Actual executing model ID (e.g. 'grok-4.5').")
    profile: Optional[str] = Field(None, description="Internal routing profile.")
    tokens: int = Field(0, description="Total tokens consumed.")
    latency_sec: float = Field(0.0)
    route: str = Field(..., description="High-level route (fast/agentic/research/etc.).")
    plane: Literal["API", "CLI", "CLI-Fallback", "local", "utility"] = Field("API")
    reasoning_effort: Optional[Literal["low", "medium", "high"]] = Field(None, description="Grok 4.5+ native reasoning level.")
    citations: Optional[List[Dict[str, str]]] = Field(None, description="Native xAI/X citations with URL + snippet.")

class ChatResult(BaseResult):
    response_id: Optional[str] = Field(None, description="Server-side stateful ID for continuation.")
    session: Optional[str] = Field(None, description="Persistent session name.")

class AgentResult(BaseResult):
    why: str = Field("auto", description="Router decision trace (Grok-native).")
    routing: Optional[Dict[str, Any]] = Field(
        None, description="Prompt-free, versioned receipt explaining model selection."
    )
    credentials: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Non-secret credential-plane health, CLI-first preference, and actions "
            "that require user approval."
        ),
    )
    degraded: bool = Field(False, description="True if fallback occurred.")
    trace: Optional[List[Dict[str, Any]]] = Field(None, description="Multi-agent step trace (for grok_agent research mode).")
    requested_mode: Optional[Literal["auto", "fast", "reasoning", "thinking", "research"]] = Field(
        None, description="Resolved public agent mode after explicit/dial/default precedence."
    )
    mode_source: Optional[Literal["explicit", "dial", "default"]] = Field(
        None, description="Where the resolved public agent mode came from."
    )
    dialed_port: Optional[int] = Field(None, description="Phoneword mode port used as the default, if any.")
    requested_plane: Optional[Literal["auto", "cli", "api"]] = Field(
        None, description="Caller-selected starting credential plane."
    )
    resolved_plane: Optional[Literal["API", "CLI", "CLI-Fallback", "local"]] = Field(
        None, description="Credential plane that actually executed the request."
    )
    fallback_policy: Optional[Literal["same_plane", "cross_plane"]] = Field(
        None, description="Whether execution may cross credential planes after failure."
    )
    billing_class: Optional[Literal["subscription", "metered"]] = Field(
        None, description="Subscription-backed or metered execution classification."
    )

class ReflectionResult(BaseResult):
    ok: bool = Field(..., description="Whether the reflection was successful.")
    critique: Dict[str, Any] = Field(..., description="Structured Grok reflection output (schema-enforced).")

class MediaResult(BaseResult):
    images: Optional[List[str]] = Field(None, description="Grok Imagine image URLs.")
    video_url: Optional[str] = Field(None)
    duration_sec: Optional[float] = Field(None)
    imagine_params: Optional[Dict[str, Any]] = Field(None, description="Original prompt + seed for reproducibility.")
    summary: Optional[str] = Field(None, description="Legacy/duplicate formatted summary of the media generation.")

class SystemResult(BaseResult):
    data: Optional[Dict[str, Any]] = Field(None, description="Structured payload for web_search / x_search / code_execution.")
