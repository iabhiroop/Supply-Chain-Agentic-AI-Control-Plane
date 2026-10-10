"""
A/B Router.
Splits traffic between two models/prompts for experimentation.
"""

import logging
import random
from typing import Dict, Any

from gateway.router.strategy import RoutingStrategy, RoutingDecision
from gateway.models.request import ChatCompletionRequest

logger = logging.getLogger(__name__)

class ABRouter(RoutingStrategy):
    """Splits traffic based on a weighted probability."""
    
    def __init__(self, model_a: str, provider_a: str, model_b: str, provider_b: str, weight_a: float = 0.5):
        self.model_a = model_a
        self.provider_a = provider_a
        self.model_b = model_b
        self.provider_b = provider_b
        self.weight_a = weight_a
        logger.info(f"Initialized A/B Router: {model_a} ({weight_a*100}%) vs {model_b} ({(1-weight_a)*100}%)")

    async def route(self, request: ChatCompletionRequest, context: Dict[str, Any]) -> RoutingDecision:
        roll = random.random()
        
        if roll < self.weight_a:
            return RoutingDecision(
                provider=self.provider_a,
                model=self.model_a,
                strategy="ab_test",
                reason=f"A/B split (rolled {roll:.2f} < {self.weight_a})"
            )
        else:
            return RoutingDecision(
                provider=self.provider_b,
                model=self.model_b,
                strategy="ab_test",
                reason=f"A/B split (rolled {roll:.2f} >= {self.weight_a})"
            )
