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
    degraded: bool = Field(False, description="True if fallback occurred.")
    trace: Optional[List[Dict[str, Any]]] = Field(None, description="Multi-agent step trace (for grok_agent research mode).")

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
