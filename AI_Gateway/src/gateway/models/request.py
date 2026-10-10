"""
Request/Response schemas — OpenAI-compatible with gateway extensions.

These are the public API contracts. Consumers send ChatCompletionRequest,
receive ChatCompletionResponse. All gateway-specific fields use the x_ prefix.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Chat Completion — Request
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    """A single message in a chat conversation."""
    role: str = Field(..., description="One of: system, user, assistant, tool")
    content: str | list[Any] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    """
    OpenAI-compatible chat completion request.
    Gateway-specific fields are prefixed with x_ and passed via headers or body.
    """
    model: str = Field(
        "",
        description="Model identifier. Empty uses the gateway default_route.",
    )
    messages: list[ChatMessage] = Field(..., min_length=1)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(None, gt=0)
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    stop: str | list[str] | None = None
    n: int | None = Field(None, ge=1, le=1, description="Only n=1 supported in v1")
    stream: bool = Field(False, description="If true, stream tokens as SSE")

    # --- Gateway extensions (optional, also settable via headers) ---
    x_routing_strategy: str | None = Field(None, description="Override routing: static, cost, quality, latency")
    x_priority: str | None = Field(None, description="Request priority: high, normal, low")
    x_cache_control: str | None = Field(None, description="Cache directive: no-cache to bypass")
    x_semantic_cache: str | None = Field(None, description="on to enable paraphrase cache for this call")
    x_agent_id: str | None = Field(None, description="Calling agent identifier for attribution")
    x_session_id: str | None = Field(None, description="Multi-turn session identifier")
    x_trace_id: str | None = Field(None, description="Workflow trace_id from Layer 2/3")


# ---------------------------------------------------------------------------
# Chat Completion — Response
# ---------------------------------------------------------------------------

class TokenUsage(BaseModel):
    """Token usage breakdown for a single request."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None


class Choice(BaseModel):
    """A single completion choice."""
    index: int = 0
    message: ChatMessage
    finish_reason: str | None = "stop"


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response with gateway metadata."""
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: TokenUsage

    # --- Gateway metadata (returned in headers too) ---
    x_provider: str | None = None
    x_cache_hit: bool = False
    x_cache_status: str | None = None
    x_cost_usd: float = 0.0
    x_route_strategy: str | None = None
    x_latency_ms: int | None = None
    x_gateway_request_id: str = Field(
        default_factory=lambda: f"gw-{uuid.uuid4().hex[:16]}"
    )
    x_fallback_used: bool = False
    x_tpm_remaining: int | None = None
    x_route_reason: str | None = None
    x_model_used: str | None = None


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

class EmbeddingRequest(BaseModel):
    """OpenAI-compatible embedding request."""
    model: str
    input: str | list[str]
    encoding_format: str | None = "float"


class EmbeddingData(BaseModel):
    """A single embedding vector."""
    object: str = "embedding"
    index: int = 0
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    """OpenAI-compatible embedding response."""
    object: str = "list"
    model: str
    data: list[EmbeddingData]
    usage: TokenUsage


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class GatewayError(BaseModel):
    """Standardized error response from the gateway."""
    error: str
    code: str
    message: str
    details: dict[str, Any] | None = None
    gateway_request_id: str = Field(
        default_factory=lambda: f"gw-{uuid.uuid4().hex[:16]}"
    )
