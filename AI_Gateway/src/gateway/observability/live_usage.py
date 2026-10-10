"""
Live usage tracker — sliding-window TPM/RPM plus minute rollups for the admin UI.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


def _minute_epoch(ts: float | None = None) -> int:
    return int((ts or time.time()) // 60)


@dataclass
class MinuteBucket:
    minute: int
    requests: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    rate_limit_hits: int = 0
    fallbacks: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    by_model: Dict[str, Dict[str, float]] = field(default_factory=dict)
    by_provider: Dict[str, Dict[str, float]] = field(default_factory=dict)
    by_project: Dict[str, Dict[str, float]] = field(default_factory=dict)
    route_mix: Dict[str, int] = field(default_factory=dict)
    errors: Dict[str, int] = field(default_factory=dict)
    latencies: List[int] = field(default_factory=list)


class LiveUsageTracker:
    """Hot-path counters that do not require scanning usage_records."""

    def __init__(self, limiter, keep_minutes: int = 60):
        self.limiter = limiter
        self.keep_minutes = keep_minutes
        self._buckets: Dict[int, MinuteBucket] = {}
        self._rate_limit_events: Deque[dict[str, Any]] = deque(maxlen=50)
        self._routing_log: Deque[dict[str, Any]] = deque(maxlen=50)
        self.in_flight: int = 0
        self.total_fallbacks: int = 0

    def _bucket(self, ts: float | None = None) -> MinuteBucket:
        minute = _minute_epoch(ts)
        bucket = self._buckets.get(minute)
        if bucket is None:
            bucket = MinuteBucket(minute=minute)
            self._buckets[minute] = bucket
        cutoff = minute - self.keep_minutes
        stale = [k for k in self._buckets if k < cutoff]
        for k in stale:
            self._buckets.pop(k, None)
        return bucket

    def begin_request(self) -> None:
        self.in_flight += 1

    def end_request(self) -> None:
        self.in_flight = max(0, self.in_flight - 1)

    def record_completed(
        self,
        *,
        provider: str,
        model: str,
        tokens: int,
        cost_usd: float,
        fallback: bool = False,
        project_id: str = "default",
        key_id: str | None = None,
        provider_key_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
        cache_hit: bool = False,
        route_strategy: str = "",
        latency_ms: int = 0,
        error_code: str = "",
    ) -> None:
        bucket = self._bucket()
        bucket.requests += 1
        bucket.tokens += tokens
        bucket.cost_usd += cost_usd
        bucket.input_tokens += input_tokens
        bucket.output_tokens += output_tokens
        bucket.cached_tokens += cached_tokens
        bucket.reasoning_tokens += reasoning_tokens
        if cache_hit:
            bucket.cache_hits += 1
        if fallback:
            bucket.fallbacks += 1
            self.total_fallbacks += 1
        if error_code:
            bucket.errors[error_code] = bucket.errors.get(error_code, 0) + 1
        if route_strategy:
            bucket.route_mix[route_strategy] = bucket.route_mix.get(route_strategy, 0) + 1
        if latency_ms:
            bucket.latencies.append(latency_ms)
            if len(bucket.latencies) > 500:
                bucket.latencies = bucket.latencies[-250:]
        label = f"{provider}/{model}"
        stats = bucket.by_model.setdefault(label, {"requests": 0, "tokens": 0, "cost": 0.0})
        stats["requests"] += 1
        stats["tokens"] += tokens
        stats["cost"] += cost_usd
        prov = bucket.by_provider.setdefault(provider or "unknown", {"requests": 0, "tokens": 0, "cost": 0.0})
        prov["requests"] += 1
        prov["tokens"] += tokens
        prov["cost"] += cost_usd
        proj = bucket.by_project.setdefault(project_id or "default", {"requests": 0, "tokens": 0, "cost": 0.0})
        proj["requests"] += 1
        proj["tokens"] += tokens
        proj["cost"] += cost_usd
        _ = (key_id, provider_key_id)

    def record_rate_limit(self, *, dimension: str, reason: str, current_rpm: int, current_tpm: int) -> None:
        bucket = self._bucket()
        bucket.rate_limit_hits += 1
        bucket.errors["RATE_LIMITED"] = bucket.errors.get("RATE_LIMITED", 0) + 1
        self._rate_limit_events.appendleft({
            "ts": time.time(),
            "dimension": dimension,
            "reason": reason,
            "rpm": current_rpm,
            "tpm": current_tpm,
        })

    def record_routing(
        self,
        *,
        provider: str,
        model: str,
        strategy: str,
        reason: str,
        candidates: Optional[List[str]] = None,
        fallback: bool = False,
    ) -> None:
        self._routing_log.appendleft({
            "ts": time.time(),
            "provider": provider,
            "model": model,
            "strategy": strategy,
            "reason": reason,
            "candidates": candidates or [],
            "fallback": fallback,
        })

    def sparkline(self, minutes: int | None = None) -> list[dict[str, Any]]:
        window = minutes or min(15, self.keep_minutes)
        now = _minute_epoch()
        points = []
        for offset in range(window - 1, -1, -1):
            minute = now - offset
            bucket = self._buckets.get(minute) or MinuteBucket(minute=minute)
            points.append({
                "minute": minute,
                "label": time.strftime("%H:%M", time.localtime(minute * 60)),
                "requests": bucket.requests,
                "tokens": bucket.tokens,
                "cost": round(bucket.cost_usd, 6),
                "rate_limit_hits": bucket.rate_limit_hits,
                "fallbacks": bucket.fallbacks,
                "cache_hits": bucket.cache_hits,
                "input_tokens": bucket.input_tokens,
                "output_tokens": bucket.output_tokens,
                "cached_tokens": bucket.cached_tokens,
                "reasoning_tokens": bucket.reasoning_tokens,
                "by_model": bucket.by_model,
                "by_project": bucket.by_project,
                "route_mix": bucket.route_mix,
                "errors": bucket.errors,
                "latencies": list(bucket.latencies[-100:]),
            })
        return points

    def current_totals(self) -> dict[str, Any]:
        now = _minute_epoch()
        bucket = self._buckets.get(now) or MinuteBucket(minute=now)
        return {
            "rpm": bucket.requests,
            "tpm": bucket.tokens,
            "cost_usd": round(bucket.cost_usd, 6),
            "rate_limit_hits": bucket.rate_limit_hits,
            "fallbacks": bucket.fallbacks,
            "by_model": bucket.by_model,
        }

    async def snapshot(self, models_flat: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        totals = self.current_totals()
        models_out: list[dict[str, Any]] = []
        for m in models_flat or []:
            provider = m.get("provider", "")
            model = m.get("model", "")
            rpm_cap = int(m.get("provider_rpm") or m.get("default_provider_rpm") or 0)
            tpm_cap = int(m.get("provider_tpm") or m.get("default_provider_tpm") or 0)
            dim = f"model:{provider}:{model}"
            peek = await self.limiter.peek(dim, rpm_cap, tpm_cap)
            models_out.append({
                "provider": provider,
                "model": model,
                "rpm": peek.current_rpm,
                "tpm": peek.current_tpm,
                "rpm_limit": rpm_cap,
                "tpm_limit": tpm_cap,
                "remaining_tpm": peek.remaining_tpm if tpm_cap else None,
                "remaining_rpm": peek.remaining_rpm if rpm_cap else None,
                "utilization": peek.utilization,
                "status": m.get("status", "active"),
            })
        return {
            "rpm": totals["rpm"],
            "tpm": totals["tpm"],
            "cost_usd": totals["cost_usd"],
            "in_flight": self.in_flight,
            "rate_limit_hits": totals["rate_limit_hits"],
            "fallbacks": totals["fallbacks"],
            "sparkline": self.sparkline(15),
            "models": models_out,
            "recent_rate_limits": list(self._rate_limit_events)[:10],
            "recent_routes": list(self._routing_log)[:10],
        }
