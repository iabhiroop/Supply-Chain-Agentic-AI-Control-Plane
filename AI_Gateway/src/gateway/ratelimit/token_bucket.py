"""
In-memory Token Bucket / Sliding Window Rate Limiter.

Windows are keyed by an arbitrary dimension string so the same limiter can
track tenant keys, provider models, and provider API keys independently.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class RateLimitResult(BaseModel):
    allowed: bool
    retry_after_seconds: Optional[float] = None
    current_rpm: int = 0
    current_tpm: int = 0
    remaining_rpm: int = 0
    remaining_tpm: int = 0
    utilization: float = 0.0
    reason: Optional[str] = None
    dimension: str = ""


@dataclass
class WindowEntry:
    timestamp: float
    tokens: int


@dataclass
class SlidingWindow:
    entries: List[WindowEntry] = field(default_factory=list)

    def cleanup(self, window_size_sec: float) -> None:
        cutoff = time.time() - window_size_sec
        self.entries = [e for e in self.entries if e.timestamp > cutoff]

    def current_rpm(self) -> int:
        return len(self.entries)

    def current_tpm(self) -> int:
        return sum(e.tokens for e in self.entries)


class RateLimiter:
    """In-memory rate limiter for development mode."""

    def __init__(self, window_size_sec: float = 60.0):
        self.window_size_sec = window_size_sec
        self._windows: Dict[str, SlidingWindow] = {}
        logger.info("Initialized in-memory RateLimiter")

    def _window(self, dimension: str) -> SlidingWindow:
        window = self._windows.get(dimension)
        if not window:
            window = SlidingWindow()
            self._windows[dimension] = window
        window.cleanup(self.window_size_sec)
        return window

    def _result(
        self,
        dimension: str,
        window: SlidingWindow,
        rpm_limit: int,
        tpm_limit: int,
        estimated_tokens: int = 0,
        allowed: bool = True,
        reason: Optional[str] = None,
    ) -> RateLimitResult:
        rpm = window.current_rpm()
        tpm = window.current_tpm()
        rpm_limit = max(rpm_limit, 0)
        tpm_limit = max(tpm_limit, 0)
        rpm_u = (rpm / rpm_limit) if rpm_limit else 0.0
        tpm_u = ((tpm + estimated_tokens) / tpm_limit) if tpm_limit else 0.0
        return RateLimitResult(
            allowed=allowed,
            retry_after_seconds=None if allowed else self.window_size_sec,
            current_rpm=rpm,
            current_tpm=tpm,
            remaining_rpm=max(0, rpm_limit - rpm) if rpm_limit else 10**9,
            remaining_tpm=max(0, tpm_limit - tpm) if tpm_limit else 10**9,
            utilization=round(max(rpm_u, tpm_u), 4),
            reason=reason,
            dimension=dimension,
        )

    async def check(
        self,
        dimension: str,
        estimated_tokens: int,
        rpm_limit: int,
        tpm_limit: int,
    ) -> RateLimitResult:
        window = self._window(dimension)
        if rpm_limit and window.current_rpm() >= rpm_limit:
            return self._result(
                dimension, window, rpm_limit, tpm_limit, estimated_tokens,
                allowed=False,
                reason=f"Exceeded requests per minute limit ({rpm_limit})",
            )
        if tpm_limit and (window.current_tpm() + estimated_tokens) > tpm_limit:
            return self._result(
                dimension, window, rpm_limit, tpm_limit, estimated_tokens,
                allowed=False,
                reason=f"Exceeded tokens per minute limit ({tpm_limit})",
            )
        return self._result(dimension, window, rpm_limit, tpm_limit, estimated_tokens)

    async def peek(self, dimension: str, rpm_limit: int = 0, tpm_limit: int = 0) -> RateLimitResult:
        window = self._window(dimension)
        return self._result(dimension, window, rpm_limit, tpm_limit)

    async def check_rate_limit(
        self,
        key_id: str,
        estimated_tokens: int,
        rpm_limit: int,
        tpm_limit: int,
    ) -> RateLimitResult:
        return await self.check(f"key:{key_id}", estimated_tokens, rpm_limit, tpm_limit)

    async def record(self, dimension: str, tokens: int) -> None:
        window = self._window(dimension)
        window.entries.append(WindowEntry(timestamp=time.time(), tokens=tokens))

    async def record_usage(self, key_id: str, tokens: int) -> None:
        await self.record(f"key:{key_id}", tokens)

    def snapshots(self, prefix: str = "") -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for dim, window in list(self._windows.items()):
            if prefix and not dim.startswith(prefix):
                continue
            window.cleanup(self.window_size_sec)
            out[dim] = {"rpm": window.current_rpm(), "tpm": window.current_tpm()}
        return out

    async def close(self) -> None:
        return None
