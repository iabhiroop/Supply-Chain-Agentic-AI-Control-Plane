"""Static routing strategy."""

from typing import Dict, Any

from gateway.router.strategy import RoutingStrategy, RoutingDecision
from gateway.models.request import ChatCompletionRequest
from gateway.config import ModelsConfig

class StaticRouter(RoutingStrategy):
    """Routes based exactly on the model requested, if it exists."""
    
    def __init__(self, models_config: ModelsConfig):
        self.models_config = models_config

    async def route(self, request: ChatCompletionRequest, context: Dict[str, Any]) -> RoutingDecision:
        for prov_name, prov_data in self.models_config.providers.items():
            if request.model in prov_data.get("models", {}):
                return RoutingDecision(
                    provider=prov_name,
                    model=request.model,
                    strategy="static",
                    reason="Model match"
                )
        
        # Fallback to gemini and whatever model they requested (will likely fail downstream if invalid)
        return RoutingDecision(
            provider="gemini",
            model=request.model,
            strategy="static",
            reason="Model not found in catalog, assuming gemini default"
        )
