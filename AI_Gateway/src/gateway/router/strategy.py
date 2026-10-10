"""Routing Strategy Interface."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from gateway.models.request import ChatCompletionRequest


class RoutingDecision(BaseModel):
    provider: str
    model: str
    strategy: str
    reason: str = ""
    candidates: List[str] = Field(default_factory=list)
    skipped: List[str] = Field(default_factory=list)
    fallback_used: bool = False


class RoutingStrategy(ABC):
    """Base class for all routing strategies."""

    @abstractmethod
    async def route(self, request: ChatCompletionRequest, context: Dict[str, Any]) -> RoutingDecision:
        """Decide which provider and model to route the request to."""
        pass
