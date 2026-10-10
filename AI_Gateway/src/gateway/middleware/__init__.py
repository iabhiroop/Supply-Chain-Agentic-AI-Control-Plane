"""
Composable Middleware Pipeline.

Each middleware is an async callable that processes a request and either:
  - Returns a response (short-circuits the pipeline, e.g. cache hit, rate limit block)
  - Returns None (passes to the next middleware)

The pipeline executes middleware in order. After the final middleware,
the request is forwarded to the provider. Post-processing middleware
(egress) runs on the response in reverse order.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from gateway.models.request import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    GatewayError,
    TokenUsage,
)
from gateway.models.key import VirtualKey

logger = logging.getLogger(__name__)


@dataclass
class RequestContext:
    """
    Carries state through the middleware pipeline for a single request.
    Every middleware reads and writes to this shared context.
    """
    # Input
    request: ChatCompletionRequest
    key: VirtualKey | None = None
    raw_headers: dict[str, str] = field(default_factory=dict)

    # Set by router
    resolved_provider: str = ""
    resolved_model: str = ""
    route_strategy: str = "static"
    route_reason: str = ""
    route_candidates: list[str] = field(default_factory=list)
    fallback_used: bool = False
    tpm_remaining: int | None = None
    budget_usage_pct: float = 0.0
    budget_remaining_usd: float | None = None
    provider_key_id: str | None = None

    # Set by metering
    estimated_input_tokens: int = 0
    estimated_cost_usd: float = 0.0

    # Set by provider call
    provider_response: Any = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    actual_cost_usd: float = 0.0

    # Performance
    start_time: float = field(default_factory=time.time)
    latency_ms: int = 0

    # Flags
    cache_hit: bool = False
    cache_status: str = "miss"
    semantic_cache: bool = False
    gateway_request_id: str = ""

    # Errors
    error: GatewayError | None = None
    error_code: str = ""
    cache_cost_avoided_usd: float = 0.0

    def elapsed_ms(self) -> int:
        return int((time.time() - self.start_time) * 1000)


class MiddlewarePipeline:
    """
    Executes a chain of middleware functions in order.
    
    Usage:
        pipeline = MiddlewarePipeline()
        pipeline.add_ingress("auth", auth_middleware)
        pipeline.add_ingress("validate", validation_middleware)
        pipeline.add_ingress("cache_check", cache_middleware)
        pipeline.add_ingress("rate_limit", rate_limit_middleware)
        pipeline.add_ingress("budget_check", budget_middleware)
        pipeline.add_egress("metering", metering_middleware)
        pipeline.add_egress("logging", logging_middleware)
        
        response = await pipeline.execute(context, provider_call)
    """

    def __init__(self):
        self._ingress: list[tuple[str, Callable]] = []
        self._egress: list[tuple[str, Callable]] = []

    def add_ingress(
        self,
        name: str,
        middleware: Callable[[RequestContext], Awaitable[ChatCompletionResponse | None]],
    ) -> None:
        """Add a pre-processing middleware. Runs before the provider call."""
        self._ingress.append((name, middleware))

    def add_egress(
        self,
        name: str,
        middleware: Callable[[RequestContext, ChatCompletionResponse], Awaitable[ChatCompletionResponse]],
    ) -> None:
        """Add a post-processing middleware. Runs after the provider call."""
        self._egress.append((name, middleware))

    async def execute(
        self,
        ctx: RequestContext,
        provider_call: Callable[[RequestContext], Awaitable[ChatCompletionResponse]],
    ) -> ChatCompletionResponse:
        """
        Execute the full pipeline:
        1. Run ingress middleware in order (any can short-circuit with a response)
        2. Call the provider
        3. Run egress middleware in order
        """
        # --- Ingress ---
        for name, middleware in self._ingress:
            try:
                early_response = await middleware(ctx)
                if early_response is not None:
                    logger.info(
                        "Middleware '%s' short-circuited pipeline",
                        name,
                        extra={"gateway_request_id": ctx.gateway_request_id},
                    )
                    # Still run egress for logging/metering on cached/blocked responses
                    return await self._run_egress(ctx, early_response)
            except Exception as e:
                logger.error(
                    "Middleware '%s' raised exception: %s",
                    name, str(e),
                    extra={"gateway_request_id": ctx.gateway_request_id},
                )
                raise

        # --- Provider Call ---
        try:
            response = await provider_call(ctx)
        except Exception as e:
            logger.error(
                "Provider call failed: %s",
                str(e),
                extra={
                    "gateway_request_id": ctx.gateway_request_id,
                    "provider": ctx.resolved_provider,
                    "model": ctx.resolved_model,
                },
            )
            raise

        # --- Egress ---
        return await self._run_egress(ctx, response)

    async def run_ingress(self, ctx: RequestContext) -> ChatCompletionResponse | None:
        """Run pre-provider middleware only. Returns a short-circuit response if any."""
        for name, middleware in self._ingress:
            try:
                early_response = await middleware(ctx)
                if early_response is not None:
                    logger.info(
                        "Middleware '%s' short-circuited pipeline",
                        name,
                        extra={"gateway_request_id": ctx.gateway_request_id},
                    )
                    return early_response
            except Exception as e:
                logger.error(
                    "Middleware '%s' raised exception: %s",
                    name, str(e),
                    extra={"gateway_request_id": ctx.gateway_request_id},
                )
                raise
        return None

    async def run_egress(
        self,
        ctx: RequestContext,
        response: ChatCompletionResponse,
    ) -> ChatCompletionResponse:
        return await self._run_egress(ctx, response)

    async def _run_egress(
        self,
        ctx: RequestContext,
        response: ChatCompletionResponse,
    ) -> ChatCompletionResponse:
        """Run all egress middleware on the response."""
        ctx.latency_ms = ctx.elapsed_ms()
        for name, middleware in self._egress:
            try:
                response = await middleware(ctx, response)
            except Exception as e:
                logger.error(
                    "Egress middleware '%s' raised exception: %s",
                    name, str(e),
                    extra={"gateway_request_id": ctx.gateway_request_id},
                )
                # Don't fail the response due to egress errors (logging, metering)
                continue
        return response
