"""OpenAI-compatible embeddings — Ollama embeddinggemma via LocalProvider."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Header, HTTPException

from gateway.dependencies import get_container
from gateway.models.request import EmbeddingData, EmbeddingRequest, EmbeddingResponse, GatewayError, TokenUsage

logger = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_EMBED_MODEL = "embeddinggemma:latest"


@router.post("/v1/embeddings", response_model=EmbeddingResponse)
async def embeddings(
    body: EmbeddingRequest,
    authorization: str | None = Header(None),
    x_trace_id: str | None = Header(None),
):
    container = get_container()
    if not container.is_initialized:
        raise HTTPException(status_code=503, detail="Gateway not initialized")

    if authorization:
        raw_key = authorization.removeprefix("Bearer ").strip()
        key = await container.key_manager.validate_key(raw_key)
        if key is None:
            raise HTTPException(status_code=401, detail="Invalid or expired API key")
        allowed = key.allowed_endpoints or []
        if allowed and "/v1/embeddings" not in allowed and "*" not in allowed:
            raise HTTPException(status_code=403, detail="Key is not permitted to call /v1/embeddings")
    elif not container.settings.is_dev_mode:
        raise HTTPException(status_code=401, detail="Missing Authorization header. Use 'Bearer vk_...'")

    texts = body.input if isinstance(body.input, list) else [body.input]
    if not texts:
        raise HTTPException(status_code=400, detail="input must be a string or non-empty list")
    model = body.model if body.model and body.model not in {"text-embedding", "text-embedding-3-small"} else _DEFAULT_EMBED_MODEL

    local = container.providers.get("local")
    if local is None or not hasattr(local, "embed"):
        raise HTTPException(status_code=502, detail="Local Ollama provider is not configured")

    request_id = f"gw-{uuid.uuid4().hex[:16]}"
    logger.info("embeddings request_id=%s trace_id=%s model=%s", request_id, x_trace_id, model)
    try:
        vectors = await local.embed(texts, model=model)
    except Exception as exc:
        logger.error("Embeddings failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=GatewayError(
                error="provider_error",
                code="PROVIDER_FAILED",
                message=str(exc),
                gateway_request_id=request_id,
            ).model_dump(),
        ) from exc

    return EmbeddingResponse(
        model=model,
        data=[EmbeddingData(index=i, embedding=vec) for i, vec in enumerate(vectors)],
        usage=TokenUsage(prompt_tokens=sum(len(t.split()) for t in texts), total_tokens=sum(len(t.split()) for t in texts)),
    )
