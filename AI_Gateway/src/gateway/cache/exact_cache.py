"""
Exact Match Cache.
Provides in-memory caching for identical prompt+model combinations.
"""

import logging
import hashlib
import json
import time
from typing import Dict, Optional, Any
from dataclasses import dataclass

from gateway.models.request import ChatCompletionRequest, ChatCompletionResponse

logger = logging.getLogger(__name__)

@dataclass
class CacheEntry:
    response: ChatCompletionResponse
    created_at: float
    ttl_seconds: int
    hit_count: int = 0
    
    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

class ExactMatchCache:
    """In-memory exact-match cache."""
    
    def __init__(self):
        self._cache: Dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0
        logger.info("Initialized ExactMatchCache")

    def _make_key(self, request: ChatCompletionRequest) -> str:
        """Create a deterministic hash for a request."""
        # Extract meaningful fields for caching
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
        return hashlib.sha256(cache_str.encode()).hexdigest()

    async def get(self, request: ChatCompletionRequest) -> Optional[ChatCompletionResponse]:
        """Retrieve a cached response if it exists and is not expired."""
        key = self._make_key(request)
        entry = self._cache.get(key)
        
        if entry:
            if entry.is_expired:
                del self._cache[key]
                self._misses += 1
                return None
            
            entry.hit_count += 1
            self._hits += 1
            # Return a copy to avoid mutating the cached object
            return entry.response.model_copy(deep=True)
            
        self._misses += 1
        return None

    async def put(self, request: ChatCompletionRequest, response: ChatCompletionResponse, ttl_seconds: int = 300) -> None:
        """Store a response in the cache."""
        key = self._make_key(request)
        self._cache[key] = CacheEntry(
            response=response.model_copy(deep=True),
            created_at=time.time(),
            ttl_seconds=ttl_seconds
        )

    async def invalidate(self, request: ChatCompletionRequest) -> None:
        """Manually invalidate a cache entry."""
        key = self._make_key(request)
        self._cache.pop(key, None)

    async def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> Dict[str, Any]:
        """Cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total) if total > 0 else 0
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate
        }
