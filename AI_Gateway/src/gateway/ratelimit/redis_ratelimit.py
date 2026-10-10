"""
Redis Sliding Window Rate Limiter.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from redis.asyncio import Redis

from gateway.ratelimit.token_bucket import RateLimitResult

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Redis-backed rate limiter using sliding windows (ZSET)."""

    def __init__(self, redis_url: str, window_size_sec: float = 60.0):
        self._redis = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        self.window_size_sec = window_size_sec
        logger.info("Initialized RedisRateLimiter connected to %s", redis_url)

    def _keys(self, dimension: str) -> tuple[str, str]:
        return f"gateway:ratelimit:rpm:{dimension}", f"gateway:ratelimit:tpm:{dimension}"

    async def _counts(self, dimension: str) -> tuple[int, int]:
        rpm_key, tpm_key = self._keys(dimension)
        now = time.time()
        window_start = now - self.window_size_sec
        pipeline = self._redis.pipeline()
        pipeline.zcount(rpm_key, window_start, "+inf")
        pipeline.zrangebyscore(tpm_key, window_start, "+inf")
        results = await pipeline.execute()
        current_rpm = int(results[0] or 0)
        current_tpm = 0
        for member in results[1] or []:
            parts = str(member).split(":")
            if len(parts) == 2:
                current_tpm += int(parts[1])
        return current_rpm, current_tpm

    def _result(
        self,
        dimension: str,
        rpm: int,
        tpm: int,
        rpm_limit: int,
        tpm_limit: int,
        estimated_tokens: int = 0,
        allowed: bool = True,
        reason: Optional[str] = None,
    ) -> RateLimitResult:
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
        try:
            current_rpm, current_tpm = await self._counts(dimension)
        except Exception as e:
            logger.warning("Redis rate limit CHECK error: %s. Failing open.", e)
            return RateLimitResult(allowed=True, current_rpm=0, current_tpm=0, dimension=dimension)

        if rpm_limit and current_rpm >= rpm_limit:
            return self._result(
                dimension, current_rpm, current_tpm, rpm_limit, tpm_limit, estimated_tokens,
                allowed=False,
                reason=f"Exceeded requests per minute limit ({rpm_limit})",
            )
        if tpm_limit and (current_tpm + estimated_tokens) > tpm_limit:
            return self._result(
                dimension, current_rpm, current_tpm, rpm_limit, tpm_limit, estimated_tokens,
                allowed=False,
                reason=f"Exceeded tokens per minute limit ({tpm_limit})",
            )
        return self._result(dimension, current_rpm, current_tpm, rpm_limit, tpm_limit, estimated_tokens)

    async def peek(self, dimension: str, rpm_limit: int = 0, tpm_limit: int = 0) -> RateLimitResult:
        try:
            rpm, tpm = await self._counts(dimension)
        except Exception as e:
            logger.warning("Redis rate limit PEEK error: %s", e)
            rpm, tpm = 0, 0
        return self._result(dimension, rpm, tpm, rpm_limit, tpm_limit)

    async def check_rate_limit(
        self,
        key_id: str,
        estimated_tokens: int,
        rpm_limit: int,
        tpm_limit: int,
    ) -> RateLimitResult:
        return await self.check(f"key:{key_id}", estimated_tokens, rpm_limit, tpm_limit)

    async def record(self, dimension: str, tokens: int) -> None:
        rpm_key, tpm_key = self._keys(dimension)
        now = time.time()
        window_start = now - self.window_size_sec
        member_id = f"{int(now * 1_000_000)}"
        tpm_member = f"{member_id}:{tokens}"
        try:
            pipeline = self._redis.pipeline()
            pipeline.zadd(rpm_key, {member_id: now})
            pipeline.zremrangebyscore(rpm_key, "-inf", window_start)
            pipeline.expire(rpm_key, int(self.window_size_sec) + 10)
            pipeline.zadd(tpm_key, {tpm_member: now})
            pipeline.zremrangebyscore(tpm_key, "-inf", window_start)
            pipeline.expire(tpm_key, int(self.window_size_sec) + 10)
            await pipeline.execute()
        except Exception as e:
            logger.warning("Redis rate limit RECORD error: %s", e)

    async def record_usage(self, key_id: str, tokens: int) -> None:
        await self.record(f"key:{key_id}", tokens)

    def snapshots(self, prefix: str = "") -> dict[str, dict[str, int]]:
        # Redis snapshots are fetched asynchronously via peek; return empty here.
        return {}

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._redis.close()
