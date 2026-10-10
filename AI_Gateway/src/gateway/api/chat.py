"""
API route: Chat Completions — POST /v1/chat/completions

This is the main endpoint. It runs the full middleware pipeline:
  Ingress (auth → validate → cache → rate limit → budget)
  → Provider call (route → send → retry)
  → Egress (token count → cost calc → log)
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from gateway.dependencies import get_container
from gateway.middleware import MiddlewarePipeline, RequestContext
from gateway.models.request import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    GatewayError,
    TokenUsage,
)
from gateway.models.key import VirtualKey
from gateway.models.usage import UsageRecord

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: str | None = Header(None),
    x_routing_strategy: str | None = Header(None),
    x_priority: str | None = Header(None),
    x_cache_control: str | None = Header(None),
    x_semantic_cache: str | None = Header(None),
    x_agent_id: str | None = Header(None),
    x_session_id: str | None = Header(None),
    x_trace_id: str | None = Header(None),
    x_route_hint: str | None = Header(None),
    x_data_sensitivity: str | None = Header(None, alias="x-data-sensitivity"),
    response: Response = None,
):
    """
    OpenAI-compatible chat completions endpoint.
    Routes to the appropriate LLM provider via the gateway pipeline.
    """
    container = get_container()
    if not container.is_initialized:
        raise HTTPException(status_code=503, detail="Gateway not initialized")

    if not (request.model or "").strip() and not (x_route_hint or "").strip():
        default = container.routes_config.default_route or {}
        request.model = default.get("model") or ""
        if not request.x_routing_strategy and default.get("strategy"):
            request.x_routing_strategy = default.get("strategy")
        if not (request.model or "").strip():
            raise HTTPException(
                status_code=400,
                detail="model omitted and default_route.model is not configured",
            )

    # Apply header overrides to request
    if x_routing_strategy:
        request.x_routing_strategy = x_routing_strategy
    if x_priority:
        request.x_priority = x_priority
    if x_cache_control:
        request.x_cache_control = x_cache_control
    if x_semantic_cache:
        request.x_semantic_cache = x_semantic_cache
    if x_agent_id:
        request.x_agent_id = x_agent_id
    if x_session_id:
        request.x_session_id = x_session_id
    if x_trace_id:
        request.x_trace_id = x_trace_id
    if x_route_hint and not request.x_routing_strategy:
        hint = x_route_hint.strip().lower()
        request.x_routing_strategy = {
            "cheap": "cost_optimized",
            "cost": "cost_optimized",
            "balanced": "usage_aware",
            "quality": "static",
        }.get(hint, hint)

    # Build request context
    ctx = RequestContext(
        request=request,
        gateway_request_id=f"gw-{uuid.uuid4().hex[:16]}",
        raw_headers={
            "authorization": authorization or "",
            "x-data-sensitivity": x_data_sensitivity or "",
            "x-trace-id": x_trace_id or "",
            "x-route-hint": x_route_hint or "",
            "x-semantic-cache": x_semantic_cache or "",
        },
    )
    ctx.semantic_cache = (request.x_semantic_cache or "").lower() in {"1", "true", "on", "yes"}

    # --- Build pipeline ---
    pipeline = MiddlewarePipeline()

    # Ingress middleware
    pipeline.add_ingress("auth", _auth_middleware)
    pipeline.add_ingress("validate", _validation_middleware)
    pipeline.add_ingress("cache_check", _cache_check_middleware)
    pipeline.add_ingress("rate_limit", _rate_limit_middleware)
    pipeline.add_ingress("budget_check", _budget_check_middleware)
    pipeline.add_ingress("route", _routing_middleware)

    # Egress middleware
    pipeline.add_egress("metering", _metering_middleware)
    pipeline.add_egress("cache_store", _cache_store_middleware)
    pipeline.add_egress("logging", _logging_middleware)

    # Execute pipeline
    container.live_usage.begin_request()
    try:
        if request.stream:
            return await _execute_streaming(pipeline, ctx, response)
        result = await pipeline.execute(ctx, _provider_call)
        _apply_gateway_headers(response, ctx, result)
        return result
    except HTTPException as exc:
        await _log_error_event(ctx, exc)
        raise
    except Exception as e:
        logger.exception("Unhandled error in chat completions pipeline")
        await _log_error_event(ctx, e, code="INTERNAL_ERROR")
        raise HTTPException(
            status_code=500,
            detail=GatewayError(
                error="internal_error",
                code="INTERNAL_ERROR",
                message=str(e),
                gateway_request_id=ctx.gateway_request_id,
            ).model_dump(),
        )
    finally:
        container.live_usage.end_request()


# ---------------------------------------------------------------------------
# Middleware implementations
# ---------------------------------------------------------------------------

async def _auth_middleware(ctx: RequestContext) -> ChatCompletionResponse | None:
    """Authenticate the request via virtual key."""
    container = get_container()

    # Extract bearer token
    auth_header = ctx.raw_headers.get("authorization", "")
    if not auth_header:
        # In dev mode, allow unauthenticated requests with a default key
        settings = container.settings
        if settings.is_dev_mode:
            logger.debug("Dev mode: allowing unauthenticated request")
            return None
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header. Use 'Bearer vk_...'",
        )

    raw_key = auth_header.removeprefix("Bearer ").strip()
    key = await container.key_manager.validate_key(raw_key)
    if key is None:
        raise HTTPException(status_code=401, detail="Invalid or expired API key")

    ctx.key = key

    # Agent Governance
    if ctx.request.x_session_id:
        container.agent_guard.track_session(ctx.request.x_session_id)
    container.agent_guard.validate_tools(ctx.request, key)

    return None


async def _validation_middleware(ctx: RequestContext) -> ChatCompletionResponse | None:
    """Validate the request schema and permissions."""
    container = get_container()
    errors = container.request_validator.validate_chat_request(ctx.request, ctx.key)
    if errors:
        raise HTTPException(
            status_code=400,
            detail=GatewayError(
                error="validation_error",
                code="VALIDATION_FAILED",
                message="; ".join(errors),
                gateway_request_id=ctx.gateway_request_id,
            ).model_dump(),
        )
    return None


async def _cache_check_middleware(ctx: RequestContext) -> ChatCompletionResponse | None:
    """Check the exact-match cache, then optional semantic cache."""
    container = get_container()

    if ctx.request.x_cache_control == "no-cache":
        ctx.cache_status = "bypass"
        return None

    cached = await container.cache.get(ctx.request, allow_semantic=ctx.semantic_cache)
    if cached is not None:
        ctx.cache_hit = True
        ctx.cache_status = getattr(container.cache, "last_status", None) or "hit"
        cached.x_cache_hit = True
        cached.x_cache_status = ctx.cache_status
        cached.x_gateway_request_id = ctx.gateway_request_id
        logger.info(
            "Cache %s for request %s",
            ctx.cache_status,
            ctx.gateway_request_id,
        )
        return cached

    ctx.cache_status = "miss"
    return None


async def _rate_limit_middleware(ctx: RequestContext) -> ChatCompletionResponse | None:
    """Check rate limits for the authenticated key."""
    container = get_container()

    if ctx.key is None:
        return None  # No key = no rate limiting (dev mode)

    # Estimate tokens for TPM tracking
    estimated_tokens = container.token_counter.estimate_request_tokens(ctx.request)
    ctx.estimated_input_tokens = estimated_tokens

    result = await container.rate_limiter.check_rate_limit(
        key_id=str(ctx.key.id),
        estimated_tokens=estimated_tokens,
        rpm_limit=ctx.key.rate_limits.rpm,
        tpm_limit=ctx.key.rate_limits.tpm,
    )

    if not result.allowed:
        ctx.error_code = "RATE_LIMITED"
        container.request_logger.log_rate_limit(str(ctx.key.id), result.reason or "Rate limit exceeded")
        if container.live_usage:
            container.live_usage.record_rate_limit(
                dimension=f"key:{ctx.key.id}",
                reason=result.reason or "Rate limit exceeded",
                current_rpm=result.current_rpm,
                current_tpm=result.current_tpm,
            )
        container.metrics_exporter.record_rate_limit("virtual_key")
        raise HTTPException(
            status_code=429,
            detail=GatewayError(
                error="rate_limit_exceeded",
                code="RATE_LIMITED",
                message=result.reason or "Rate limit exceeded",
                details={
                    "retry_after_seconds": result.retry_after_seconds,
                    "current_rpm": result.current_rpm,
                    "current_tpm": result.current_tpm,
                },
                gateway_request_id=ctx.gateway_request_id,
            ).model_dump(),
            headers={"Retry-After": str(int(result.retry_after_seconds or 60))},
        )

    from gateway.governance.named_agents import limits_for, scope_id as agent_scope

    named = limits_for(ctx.request.x_agent_id)
    if named:
        agent_result = await container.rate_limiter.check(
            agent_scope(ctx.request.x_agent_id),
            estimated_tokens,
            int(named["rpm"]),
            int(named["tpm"]),
        )
        if not agent_result.allowed:
            ctx.error_code = "RATE_LIMITED"
            raise HTTPException(
                status_code=429,
                detail=GatewayError(
                    error="rate_limit_exceeded",
                    code="AGENT_RATE_LIMITED",
                    message=f"{ctx.request.x_agent_id} rpm/tpm bucket exhausted",
                    details={
                        "agent_id": ctx.request.x_agent_id,
                        "retry_after_seconds": agent_result.retry_after_seconds,
                        "current_rpm": agent_result.current_rpm,
                    },
                    gateway_request_id=ctx.gateway_request_id,
                ).model_dump(),
                headers={"Retry-After": str(int(agent_result.retry_after_seconds or 60))},
            )

    return None


async def _budget_check_middleware(ctx: RequestContext) -> ChatCompletionResponse | None:
    """Check budget limits before sending to provider."""
    container = get_container()

    ctx.estimated_cost_usd = container.cost_calculator.estimate_cost(
        provider="gemini",
        model=ctx.request.model,
        estimated_input_tokens=ctx.estimated_input_tokens,
    )

    from gateway.governance.named_agents import limits_for, scope_id as agent_scope

    named = limits_for(ctx.request.x_agent_id)
    if named:
        scope = agent_scope(ctx.request.x_agent_id)
        limit = float(named["daily_usd"])
        await container.budget_manager.ensure_budget(scope, limit)
        result = await container.budget_manager.check_budget(scope, ctx.estimated_cost_usd)
        ctx.budget_usage_pct = result.usage_pct or 0.0
        ctx.budget_remaining_usd = result.remaining_usd
        exhausted = (not result.allowed) or limit <= 0
        if exhausted:
            ctx.error_code = "BUDGET_EXCEEDED"
            raise HTTPException(
                status_code=429,
                detail=GatewayError(
                    error="budget_exceeded",
                    code="AGENT_BUDGET_EXCEEDED",
                    message=f"{ctx.request.x_agent_id} daily $ bucket exhausted",
                    details={
                        "agent_id": ctx.request.x_agent_id,
                        "remaining_usd": 0.0 if limit <= 0 else result.remaining_usd,
                        "usage_pct": 100.0 if limit <= 0 else result.usage_pct,
                    },
                    gateway_request_id=ctx.gateway_request_id,
                ).model_dump(),
            )

    if ctx.key is None:
        return None

    scope_id = ctx.key.project_id
    await container.budget_manager.ensure_budget(
        scope_id, container.settings.default_daily_budget_usd
    )
    result = await container.budget_manager.check_budget(scope_id, ctx.estimated_cost_usd)
    if ctx.budget_remaining_usd is None:
        ctx.budget_remaining_usd = result.remaining_usd
    ctx.budget_usage_pct = result.usage_pct or ctx.budget_usage_pct

    if not result.allowed:
        ctx.error_code = "BUDGET_EXCEEDED"
        raise HTTPException(
            status_code=429,
            detail=GatewayError(
                error="budget_exceeded",
                code="BUDGET_EXCEEDED",
                message=result.reason or "Budget limit exceeded",
                details={
                    "remaining_usd": result.remaining_usd,
                    "usage_pct": result.usage_pct,
                },
                gateway_request_id=ctx.gateway_request_id,
            ).model_dump(),
        )

    return None


async def _routing_middleware(ctx: RequestContext) -> ChatCompletionResponse | None:
    """Resolve which provider and model to use."""
    container = get_container()

    decision = await container.router.route(
        ctx.request,
        ctx.key,
        estimated_tokens=ctx.estimated_input_tokens,
        budget_usage_pct=ctx.budget_usage_pct,
        headers={k.lower(): v for k, v in ctx.raw_headers.items()},
    )
    ctx.resolved_provider = decision.provider
    ctx.resolved_model = decision.model
    ctx.route_strategy = decision.strategy
    ctx.route_reason = decision.reason
    ctx.route_candidates = list(getattr(decision, "candidates", None) or [])
    ctx.fallback_used = decision.reason not in ("primary", "Model match", "") and "Matched rule" not in (decision.reason or "")

    if container.live_usage:
        container.live_usage.record_routing(
            provider=decision.provider,
            model=decision.model,
            strategy=decision.strategy,
            reason=decision.reason,
            candidates=ctx.route_candidates,
            fallback=ctx.fallback_used,
        )

    logger.info(
        "Routed request %s → %s/%s (strategy=%s)",
        ctx.gateway_request_id,
        decision.provider,
        decision.model,
        decision.strategy,
    )
    return None


_RETRYABLE_CODES = {
    "rate_limited",
    "rate_limit_exceeded",
    "provider_timeout",
    "timeout",
    "provider_server_error",
    "provider_unavailable",
    "provider_empty_response",
}


def _is_sensitive_data_route(ctx: RequestContext) -> bool:
    """Privacy rule: never send a local-targeted sensitive request to Gemini."""
    if "sensitive-data-routing" in (ctx.route_reason or "") or ctx.route_strategy == "sensitive-data-routing":
        return True
    header = (ctx.raw_headers.get("x-data-sensitivity") or "").strip().lower()
    return header == "high"


def _gateway_error_payload(exc: Exception) -> dict | None:
    if not isinstance(exc, ValueError) or not exc.args:
        return None
    raw = exc.args[0]
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _is_retryable_provider_error(exc: Exception) -> bool:
    payload = _gateway_error_payload(exc)
    if payload and payload.get("code") in _RETRYABLE_CODES:
        return True
    message = str(exc).lower()
    if "circuit breaker is open" in message:
        return True
    return False


def _is_empty_completion(provider_response) -> bool:
    content = getattr(provider_response, "content", None)
    if isinstance(content, str):
        has_content = bool(content.strip())
    else:
        has_content = bool(content)
    tools = getattr(provider_response, "tool_calls", None) or []
    return not has_content and not tools


def _raise_provider_failed(ctx: RequestContext, provider_name: str, exc: Exception) -> None:
    logger.error(
        "Provider %s failed for request %s: %s",
        provider_name, ctx.gateway_request_id, str(exc),
    )
    payload = _gateway_error_payload(exc) or {}
    ctx.error_code = payload.get("code") or "PROVIDER_FAILED"
    status = 429 if str(ctx.error_code).lower() in {"rate_limited", "rate_limit_exceeded"} else 502
    headers = {"Retry-After": "60"} if status == 429 else None
    raise HTTPException(
        status_code=status,
        detail=GatewayError(
            error="provider_error" if status == 502 else "rate_limit_exceeded",
            code=ctx.error_code,
            message=f"Provider '{provider_name}' error: {str(exc)}",
            gateway_request_id=ctx.gateway_request_id,
        ).model_dump(),
        headers=headers,
    )


def _apply_gateway_headers(response: Response | None, ctx: RequestContext, result: ChatCompletionResponse) -> None:
    if response is None:
        return
    response.headers["x-request-id"] = ctx.gateway_request_id
    response.headers["x-model-used"] = ctx.resolved_model or result.model
    response.headers["x-provider"] = ctx.resolved_provider or result.x_provider or ""
    response.headers["x-fallback-used"] = "true" if ctx.fallback_used else "false"
    response.headers["x-route-reason"] = ctx.route_reason or ""
    if ctx.tpm_remaining is not None:
        response.headers["x-tpm-remaining"] = str(ctx.tpm_remaining)
    if result.x_cost_usd:
        response.headers["x-cost-usd"] = str(result.x_cost_usd)
    response.headers["x-cache-hit"] = "true" if (ctx.cache_hit or result.x_cache_hit) else "false"
    response.headers["x-cache-status"] = ctx.cache_status or ("hit" if ctx.cache_hit else "miss")
    if ctx.budget_remaining_usd is not None:
        response.headers["x-budget-remaining-usd"] = str(ctx.budget_remaining_usd)
    if ctx.request.x_agent_id:
        response.headers["x-agent-id"] = ctx.request.x_agent_id


def _stream_replay(result: ChatCompletionResponse) -> StreamingResponse:
    content = ""
    if result.choices:
        content = result.choices[0].message.content or ""
    chunk = {
        "id": result.id,
        "object": "chat.completion.chunk",
        "created": result.created,
        "model": result.model,
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "content": content},
            "finish_reason": result.choices[0].finish_reason if result.choices else "stop",
        }],
    }
    done = {"id": result.id, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

    async def gen():
        yield f"data: {json.dumps(chunk)}\n\n"
        yield f"data: {json.dumps(done)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _provider_hops(ctx: RequestContext) -> list[dict]:
    container = get_container()
    hops = [{"provider": ctx.resolved_provider, "model": ctx.resolved_model}]
    if not _is_sensitive_data_route(ctx):
        hops.extend(container.router.fallback_chain_for(ctx.resolved_provider, ctx.resolved_model))
    return hops


def _response_from_provider(ctx: RequestContext, provider_response) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        model=ctx.resolved_model,
        choices=[
            Choice(
                index=0,
                message=ChatMessage(
                    role=provider_response.role,
                    content=provider_response.content,
                    tool_calls=provider_response.tool_calls,
                ),
                finish_reason=provider_response.finish_reason,
            )
        ],
        usage=provider_response.usage,
        x_provider=ctx.resolved_provider,
        x_cache_hit=ctx.cache_hit,
        x_route_strategy=ctx.route_strategy,
        x_gateway_request_id=ctx.gateway_request_id,
        x_fallback_used=ctx.fallback_used,
        x_tpm_remaining=ctx.tpm_remaining,
        x_route_reason=ctx.route_reason,
        x_model_used=ctx.resolved_model,
    )


async def _execute_streaming(pipeline: MiddlewarePipeline, ctx: RequestContext, http_response: Response | None):
    """Ingress, then live token SSE, then egress metering/logging."""
    container = get_container()
    early = await pipeline.run_ingress(ctx)
    if early is not None:
        result = await pipeline.run_egress(ctx, early)
        _apply_gateway_headers(http_response, ctx, result)
        return _stream_replay(result)

    async def gen():
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())
        model_name = ctx.resolved_model
        yield _sse({
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        })
        hops = _provider_hops(ctx)
        seen: set[tuple[str, str]] = set()
        last_exc: Exception | None = None
        used_fallback = False
        fallback_reason = ctx.route_reason
        provider_response = None
        streamed_any = False
        ttft_ms = None
        start = time.time()

        for hop in hops:
            provider_name = hop.get("provider") or ctx.resolved_provider
            hop_model = hop.get("model") or ctx.resolved_model
            pair = (provider_name, hop_model)
            if pair in seen:
                continue
            seen.add(pair)
            if container.circuit_breaker.is_open(provider_name):
                last_exc = Exception(f"Circuit breaker is OPEN ({provider_name})")
                continue
            provider = container.providers.get(provider_name)
            if provider is None:
                last_exc = Exception(f"Provider '{provider_name}' is not configured")
                continue
            ctx.request.model = hop_model
            try:
                async for piece in provider.stream_text(ctx.request):
                    if ttft_ms is None:
                        ttft_ms = int((time.time() - start) * 1000)
                    streamed_any = True
                    yield _sse({
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": hop_model,
                        "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                    })
                provider_response = provider.last_stream_result
            except Exception as exc:
                last_exc = exc
                if streamed_any:
                    raise
                if _is_retryable_provider_error(exc):
                    used_fallback = True
                    payload = _gateway_error_payload(exc) or {}
                    fallback_reason = payload.get("code") or "provider_429"
                    continue
                _raise_provider_failed(ctx, provider_name, exc)

            if provider_response is None or (_is_empty_completion(provider_response) and not streamed_any):
                last_exc = Exception("empty_completion")
                used_fallback = True
                fallback_reason = "empty_completion"
                provider_response = None
                continue

            ctx.resolved_provider = provider_name
            ctx.resolved_model = hop_model
            if used_fallback:
                ctx.fallback_used = True
                ctx.route_reason = fallback_reason
                ctx.route_strategy = "fallback"
            break

        if provider_response is None:
            if last_exc is not None:
                _raise_provider_failed(ctx, ctx.resolved_provider, last_exc)
            raise HTTPException(status_code=502, detail="No provider produced a response")

        ctx.provider_response = provider_response
        if ttft_ms is not None:
            ctx.route_candidates = list(ctx.route_candidates or [])
            ctx.cache_cost_avoided_usd = ctx.cache_cost_avoided_usd
        result = _response_from_provider(ctx, provider_response)
        result = await pipeline.run_egress(ctx, result)
        if result.usage and result.usage.total_tokens == 0 and result.choices:
            text = result.choices[0].message.content or ""
            approx = max(1, len(text.split()))
            result.usage.completion_tokens = approx
            result.usage.total_tokens = (result.usage.prompt_tokens or 0) + approx
        finish = result.choices[0].finish_reason if result.choices else "stop"
        yield _sse({
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": ctx.resolved_model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            "usage": result.usage.model_dump() if result.usage else {},
        })
        yield "data: [DONE]\n\n"

    headers = {
        "x-request-id": ctx.gateway_request_id,
        "x-model-used": ctx.resolved_model or "",
        "x-provider": ctx.resolved_provider or "",
        "x-fallback-used": "true" if ctx.fallback_used else "false",
        "x-route-reason": ctx.route_reason or "",
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


async def _call_one_provider(container, provider_name: str, request):
    provider = container.providers.get(provider_name)
    if provider is None:
        raise HTTPException(status_code=502, detail=f"Provider '{provider_name}' is not configured")
    breaker = container.circuit_breaker.get(provider_name)

    async def _invoke():
        return await container.retry_engine.execute_with_retry(provider.complete, request)

    return await breaker.call(_invoke)


async def _provider_call(ctx: RequestContext) -> ChatCompletionResponse:
    """Forward the request to the resolved LLM provider, with overflow fallback."""
    container = get_container()

    hops = _provider_hops(ctx)
    seen: set[tuple[str, str]] = set()
    last_exc: Exception | None = None
    provider_response = None
    used_fallback = False
    fallback_reason = ctx.route_reason

    for hop in hops:
        provider_name = hop.get("provider") or ctx.resolved_provider
        model_name = hop.get("model") or ctx.resolved_model
        pair = (provider_name, model_name)
        if pair in seen:
            continue
        seen.add(pair)
        if container.circuit_breaker.is_open(provider_name):
            last_exc = Exception(f"Circuit breaker is OPEN ({provider_name})")
            continue
        ctx.request.model = model_name
        try:
            provider_response = await _call_one_provider(container, provider_name, ctx.request)
        except Exception as exc:
            last_exc = exc
            if _is_retryable_provider_error(exc):
                logger.warning(
                    "Provider %s/%s failed (%s); trying next hop",
                    provider_name, model_name, exc,
                )
                container.metrics_exporter.record_fallback(
                    ctx.resolved_provider, provider_name, "provider_error"
                )
                used_fallback = True
                payload = _gateway_error_payload(exc) or {}
                fallback_reason = payload.get("code") or "provider_429"
                continue
            _raise_provider_failed(ctx, provider_name, exc)

        if provider_response is not None and _is_empty_completion(provider_response):
            last_exc = Exception("empty_completion")
            used_fallback = True
            fallback_reason = "empty_completion"
            provider_response = None
            continue

        ctx.resolved_provider = provider_name
        ctx.resolved_model = model_name
        if used_fallback:
            ctx.fallback_used = True
            ctx.route_reason = fallback_reason
            ctx.route_strategy = "fallback"
        break

    if provider_response is None:
        if last_exc is not None:
            _raise_provider_failed(ctx, ctx.resolved_provider, last_exc)
        raise HTTPException(status_code=502, detail="No provider produced a response")

    ctx.provider_response = provider_response
    limits = container.models_config.get_model_limits(ctx.resolved_provider, ctx.resolved_model)
    peek = await container.rate_limiter.peek(
        f"model:{ctx.resolved_provider}:{ctx.resolved_model}",
        limits.get("provider_rpm") or 0,
        limits.get("provider_tpm") or 0,
    )
    ctx.tpm_remaining = peek.remaining_tpm if limits.get("provider_tpm") else None

    return _response_from_provider(ctx, provider_response)


async def _metering_middleware(
    ctx: RequestContext,
    response: ChatCompletionResponse,
) -> ChatCompletionResponse:
    """Count tokens and calculate cost."""
    container = get_container()

    # Token counting is already done by the provider adapter (in provider_response.usage)
    ctx.usage = response.usage

    # Calculate cost
    cost = container.cost_calculator.calculate_cost(
        provider=ctx.resolved_provider,
        model=ctx.resolved_model,
        usage=response.usage,
    )
    if ctx.cache_hit:
        ctx.cache_cost_avoided_usd = cost
        ctx.actual_cost_usd = 0.0
        response.x_cost_usd = 0.0
        cost = 0.0
    else:
        ctx.actual_cost_usd = cost
        response.x_cost_usd = cost
        if ctx.key is not None:
            await container.budget_manager.record_spend(
                ctx.key.project_id,
                cost,
                default_limit_usd=container.settings.default_daily_budget_usd,
            )
        from gateway.governance.named_agents import limits_for, scope_id as agent_scope

        named = limits_for(ctx.request.x_agent_id)
        if named and ctx.request.x_agent_id:
            await container.budget_manager.record_spend(
                agent_scope(ctx.request.x_agent_id),
                cost,
                default_limit_usd=float(named["daily_usd"]),
            )

    tokens = response.usage.total_tokens if response.usage else 0
    if ctx.key is not None:
        await container.rate_limiter.record_usage(str(ctx.key.id), tokens)
    if ctx.request.x_agent_id:
        from gateway.governance.named_agents import scope_id as agent_scope

        await container.rate_limiter.record(agent_scope(ctx.request.x_agent_id), tokens)
    await container.rate_limiter.record(
        f"model:{ctx.resolved_provider}:{ctx.resolved_model}", tokens
    )
    limits = container.models_config.get_model_limits(ctx.resolved_provider, ctx.resolved_model)
    peek = await container.rate_limiter.peek(
        f"model:{ctx.resolved_provider}:{ctx.resolved_model}",
        limits.get("provider_rpm") or 0,
        limits.get("provider_tpm") or 0,
    )
    ctx.tpm_remaining = peek.remaining_tpm if limits.get("provider_tpm") else ctx.tpm_remaining
    response.x_tpm_remaining = ctx.tpm_remaining
    container.metrics_exporter.set_live_window(
        ctx.resolved_provider, ctx.resolved_model, peek.current_rpm, peek.current_tpm
    )
    if peek.utilization >= 0.8:
        await container.alert_manager.maybe_tpm(
            ctx.resolved_provider, ctx.resolved_model, peek.utilization
        )

    if container.live_usage:
        container.live_usage.record_completed(
            provider=ctx.resolved_provider,
            model=ctx.resolved_model,
            tokens=tokens,
            cost_usd=cost,
            fallback=ctx.fallback_used,
            project_id=ctx.key.project_id if ctx.key else "default",
            key_id=str(ctx.key.id) if ctx.key else None,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            cached_tokens=(response.usage.cached_tokens or 0) if response.usage else 0,
            reasoning_tokens=(response.usage.reasoning_tokens or 0) if response.usage else 0,
            cache_hit=ctx.cache_hit,
            route_strategy=ctx.route_strategy,
            latency_ms=ctx.elapsed_ms(),
            error_code=ctx.error_code,
        )

    return response


async def _cache_store_middleware(
    ctx: RequestContext,
    response: ChatCompletionResponse,
) -> ChatCompletionResponse:
    """Store the response in cache if it's a cacheable request."""
    container = get_container()

    # Don't cache if it was already a cache hit
    if ctx.cache_hit:
        return response

    # Don't cache error responses or tool-call responses
    if response.choices and response.choices[0].finish_reason == "tool_calls":
        return response

    await container.cache.put(
        ctx.request, response, ttl_seconds=300, allow_semantic=ctx.semantic_cache
    )
    return response


async def _logging_middleware(
    ctx: RequestContext,
    response: ChatCompletionResponse,
) -> ChatCompletionResponse:
    """Log the request/response as a UsageRecord."""
    container = get_container()

    record = UsageRecord(
        gateway_request_id=ctx.gateway_request_id,
        key_id=ctx.key.id if ctx.key else None,
        organization_id=ctx.key.organization_id if ctx.key else "anonymous",
        team_id=ctx.key.team_id if ctx.key else "anonymous",
        project_id=ctx.key.project_id if ctx.key else "anonymous",
        agent_id=ctx.request.x_agent_id,
        provider=ctx.resolved_provider or response.x_provider or "",
        model=ctx.resolved_model or response.model,
        endpoint="/v1/chat/completions",
        input_tokens=ctx.usage.prompt_tokens,
        output_tokens=ctx.usage.completion_tokens,
        total_tokens=ctx.usage.total_tokens,
        cached_tokens=ctx.usage.cached_tokens or 0,
        reasoning_tokens=ctx.usage.reasoning_tokens or 0,
        cost_usd=ctx.actual_cost_usd,
        latency_ms=ctx.elapsed_ms(),
        route_strategy=ctx.route_strategy,
        cache_hit=ctx.cache_hit,
        fallback_used=ctx.fallback_used,
        route_reason=ctx.route_reason or "",
        error_code=ctx.error_code or "",
        tpm_remaining=ctx.tpm_remaining,
        cache_cost_avoided_usd=ctx.cache_cost_avoided_usd,
        metadata={
            "candidates": ctx.route_candidates,
            "tpm_remaining": ctx.tpm_remaining,
            "fallback_used": ctx.fallback_used,
            "route_reason": ctx.route_reason or "",
            "error_code": ctx.error_code or "",
            "cache_cost_avoided_usd": ctx.cache_cost_avoided_usd,
            "ttft_ms": ctx.latency_ms if ctx.cache_hit else None,
            "trace_id": getattr(ctx.request, "x_trace_id", None),
            "cache_status": ctx.cache_status or ("hit" if ctx.cache_hit else "miss"),
            "semantic_cache": bool(ctx.semantic_cache),
        },
    )

    logger.info(
        "chat complete request_id=%s trace_id=%s model=%s",
        ctx.gateway_request_id,
        getattr(ctx.request, "x_trace_id", None),
        ctx.resolved_model,
    )
    await container.request_logger.async_log_request(record)
    container.metrics_exporter.record_usage(record)

    # Set final latency on response
    response.x_latency_ms = ctx.elapsed_ms()

    return response


def _extract_error_code(exc: Exception, fallback: str = "") -> str:
    if fallback:
        return fallback
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return str(detail.get("code") or detail.get("error") or "")
        if exc.status_code == 429:
            return "RATE_LIMITED"
        if exc.status_code >= 500:
            return "PROVIDER_FAILED"
    return "ERROR"


async def _log_error_event(ctx: RequestContext, exc: Exception, code: str = "") -> None:
    """Persist failed requests so reliability charts include 429s and provider errors."""
    ctx.error_code = ctx.error_code or _extract_error_code(exc, code)
    container = get_container()
    record = UsageRecord(
        gateway_request_id=ctx.gateway_request_id,
        key_id=ctx.key.id if ctx.key else None,
        organization_id=ctx.key.organization_id if ctx.key else "anonymous",
        team_id=ctx.key.team_id if ctx.key else "anonymous",
        project_id=ctx.key.project_id if ctx.key else "anonymous",
        agent_id=ctx.request.x_agent_id if ctx.request else None,
        provider=ctx.resolved_provider,
        model=ctx.resolved_model or (ctx.request.model if ctx.request else ""),
        endpoint="/v1/chat/completions",
        cost_usd=0.0,
        latency_ms=ctx.elapsed_ms(),
        route_strategy=ctx.route_strategy,
        cache_hit=ctx.cache_hit,
        fallback_used=ctx.fallback_used,
        route_reason=ctx.route_reason or "",
        error_code=ctx.error_code,
        tpm_remaining=ctx.tpm_remaining,
        cache_cost_avoided_usd=ctx.cache_cost_avoided_usd,
        metadata={
            "error": str(exc)[:400],
            "trace_id": getattr(ctx.request, "x_trace_id", None) if ctx.request else None,
        },
    )
    try:
        await container.request_logger.async_log_request(record)
    except Exception:
        logger.exception("Failed to persist error usage record")
    if container.live_usage and ctx.error_code != "RATE_LIMITED":
        container.live_usage.record_completed(
            provider=ctx.resolved_provider,
            model=ctx.resolved_model or (ctx.request.model if ctx.request else "unknown"),
            tokens=0,
            cost_usd=0.0,
            fallback=ctx.fallback_used,
            project_id=ctx.key.project_id if ctx.key else "default",
            key_id=str(ctx.key.id) if ctx.key else None,
            cache_hit=False,
            route_strategy=ctx.route_strategy,
            latency_ms=ctx.elapsed_ms(),
            error_code=ctx.error_code,
        )
