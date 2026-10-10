"""Sticky-session routing — pin a conversation to one model for coherence."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from gateway.config import ModelsConfig
from gateway.models.request import ChatCompletionRequest
from gateway.router.strategy import RoutingDecision, RoutingStrategy


class StickySessionRouter(RoutingStrategy):
    def __init__(self, models_config: ModelsConfig, pool: list[tuple[str, str]] | None = None):
        self.models_config = models_config
        self.pool = pool or []
        self._pins: Dict[str, tuple[str, str]] = {}

    async def route(self, request: ChatCompletionRequest, context: Dict[str, Any]) -> RoutingDecision:
        session_id: Optional[str] = request.x_session_id or context.get("session_id")
        if session_id and session_id in self._pins:
            provider, model = self._pins[session_id]
            return RoutingDecision(
                provider=provider,
                model=model,
                strategy="sticky_session",
                reason="pinned",
            )

        pool = self.pool
        if not pool:
            pool = [
                (row["provider"], row["model"])
                for row in self.models_config.get_all_models_flat()
                if row.get("status") in (None, "active") and "chat" in (row.get("capabilities") or ["chat"])
            ]
        if not pool:
            found = self.models_config.find_model(request.model)
            provider, model = (found[0], request.model) if found else ("gemini", request.model)
        else:
            seed = session_id or request.model
            idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(pool)
            provider, model = pool[idx]

        if session_id:
            self._pins[session_id] = (provider, model)

        return RoutingDecision(
            provider=provider,
            model=model,
            strategy="sticky_session",
            reason="new_pin" if session_id else "hashed",
        )
