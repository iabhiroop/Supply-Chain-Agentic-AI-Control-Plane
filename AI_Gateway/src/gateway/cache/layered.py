"""Exact cache first, then optional semantic (paraphrase) lookup."""

from __future__ import annotations

from typing import Any

from gateway.cache.semantic import SemanticStore
from gateway.models.request import ChatCompletionRequest, ChatCompletionResponse


class LayeredCache:
    def __init__(self, exact: Any):
        self.exact = exact
        self.semantic = SemanticStore()
        self.last_status = "miss"

    async def get(self, request: ChatCompletionRequest, *, allow_semantic: bool = False) -> ChatCompletionResponse | None:
        hit = await self.exact.get(request)
        if hit is not None:
            self.last_status = "hit"
            return hit
        if allow_semantic:
            sem = self.semantic.get(request)
            if sem is not None:
                self.last_status = "semantic_hit"
                return sem
        self.last_status = "miss"
        return None

    async def put(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
        ttl_seconds: int = 300,
        *,
        allow_semantic: bool = False,
    ) -> None:
        await self.exact.put(request, response, ttl_seconds=ttl_seconds)
        if allow_semantic:
            self.semantic.put(request, response, ttl_seconds=ttl_seconds)

    async def invalidate(self, request: ChatCompletionRequest) -> None:
        if hasattr(self.exact, "invalidate"):
            await self.exact.invalidate(request)

    @property
    def stats(self) -> dict[str, Any]:
        base = dict(getattr(self.exact, "stats", {}) or {})
        base["semantic_hits"] = self.semantic.hits
        base["semantic_misses"] = self.semantic.misses
        return base
