"""
Redis Exact Match Cache.
Provides distributed caching for identical prompt+model combinations.
"""

import logging
import hashlib
import json
from typing import Optional, Any
from redis.asyncio import Redis

from gateway.models.request import ChatCompletionRequest, ChatCompletionResponse

logger = logging.getLogger(__name__)

class RedisExactMatchCache:
    """Redis-backed exact-match cache."""
    
    def __init__(self, redis_url: str):
        self._redis = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        self._hits = 0
        self._misses = 0
        logger.info(f"Initialized RedisExactMatchCache connected to {redis_url}")

    def _make_key(self, request: ChatCompletionRequest) -> str:
        """Create a deterministic hash for a request."""
        messages = [m.model_dump(exclude_none=True) for m in request.messages]
        tools = request.tools or []
        
        cache_dict = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "tools": tools,
            "response_format": request.response_format,
        }
        
        cache_str = json.dumps(cache_dict, sort_keys=True)
        key_hash = hashlib.sha256(cache_str.encode()).hexdigest()
        return f"gateway:cache:exact:{key_hash}"

    async def get(self, request: ChatCompletionRequest) -> Optional[ChatCompletionResponse]:
        """Retrieve a cached response if it exists."""
        key = self._make_key(request)
        
        try:
            cached_str = await self._redis.get(key)
            if cached_str:
                self._hits += 1
                return ChatCompletionResponse.model_validate_json(cached_str)
        except Exception as e:
            logger.warning(f"Redis cache GET error: {e}")
            
        self._misses += 1
        return None

    async def put(self, request: ChatCompletionRequest, response: ChatCompletionResponse, ttl_seconds: int = 300) -> None:
        """Store a response in the cache."""
        key = self._make_key(request)
        
        try:
            await self._redis.setex(
                name=key,
                time=ttl_seconds,
                value=response.model_dump_json(exclude_none=True)
            )
        except Exception as e:
            logger.warning(f"Redis cache PUT error: {e}")

    async def invalidate(self, request: ChatCompletionRequest) -> None:
        """Manually invalidate a cache entry."""
        key = self._make_key(request)
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.warning(f"Redis cache DELETE error: {e}")

    @property
    def stats(self) -> dict[str, Any]:
        """Cache statistics (local to this instance, real global stats would query Redis INFO)."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total) if total > 0 else 0
        return {
            "local_hits": self._hits,
            "local_misses": self._misses,
            "local_hit_rate": hit_rate
        }

    async def close(self) -> None:
        """Close the Redis connection pool."""
        await self._redis.close()
