"""Alert manager — webhook notifications for TPM, breakers, and spend spikes."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)


class AlertManager:
    def __init__(self):
        self.webhooks: List[str] = []
        self._last_sent: Dict[str, float] = {}
        self._cooldown_sec = 60.0
        self.history: List[dict[str, Any]] = []

    def set_webhooks(self, urls: list[str]) -> None:
        self.webhooks = [u for u in urls if u]

    async def emit(self, event: str, payload: dict[str, Any], *, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_sent.get(event, 0)) < self._cooldown_sec:
            return
        self._last_sent[event] = now
        body = {"event": event, "payload": payload, "ts": now}
        self.history.insert(0, body)
        self.history = self.history[:50]
        if not self.webhooks:
            logger.warning("Alert %s: %s", event, payload)
            return
        async with httpx.AsyncClient(timeout=5.0) as client:
            for url in self.webhooks:
                try:
                    await client.post(url, json=body)
                except Exception as exc:
                    logger.warning("Alert webhook %s failed: %s", url, exc)

    async def maybe_tpm(self, provider: str, model: str, utilization: float) -> None:
        if utilization >= 0.8:
            await self.emit(
                "tpm_high",
                {"provider": provider, "model": model, "utilization": utilization},
            )

    async def maybe_breaker(self, name: str, state: str) -> None:
        if state == "open":
            await self.emit("circuit_open", {"name": name, "state": state})

    async def maybe_spend_spike(self, project_id: str, cost_usd: float, baseline: float) -> None:
        if baseline > 0 and cost_usd > baseline * 3:
            await self.emit(
                "spend_spike",
                {"project_id": project_id, "cost_usd": cost_usd, "baseline": baseline},
            )
