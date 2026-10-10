"""Cost-optimised routing — cheapest active chat model that meets capabilities."""

from __future__ import annotations

from typing import Any, Dict

from gateway.config import ModelsConfig
from gateway.models.request import ChatCompletionRequest
from gateway.router.strategy import RoutingDecision, RoutingStrategy


class CostOptimizedRouter(RoutingStrategy):
    def __init__(self, models_config: ModelsConfig):
        self.models_config = models_config

    async def route(self, request: ChatCompletionRequest, context: Dict[str, Any]) -> RoutingDecision:
        ranked = self.models_config.cheapest_chat_models()
        if not ranked:
            return RoutingDecision(
                provider="gemini",
                model=request.model,
                strategy="cost_optimized",
                reason="no_catalog",
            )
        provider, model, _cost = ranked[0]
        return RoutingDecision(
            provider=provider,
            model=model,
            strategy="cost_optimized",
            reason="cheapest_active",
            candidates=[f"{p}/{m}" for p, m, _ in ranked[:5]],
        )
