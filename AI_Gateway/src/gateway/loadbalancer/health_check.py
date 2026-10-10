"""Health check monitoring for providers."""

import logging
from datetime import datetime
from typing import Dict, Callable, Any
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class ProviderHealth(BaseModel):
    is_healthy: bool = True
    last_check: datetime = Field(default_factory=datetime.utcnow)
    consecutive_failures: int = 0
    latency_ms: float = 0.0

class HealthChecker:
    """Monitors health of LLM providers."""
    
    def __init__(self):
        self._provider_health: Dict[str, ProviderHealth] = {}
        
    async def check_provider(self, provider_name: str, check_fn: Callable) -> bool:
        """Run a health check function for a provider."""
        if provider_name not in self._provider_health:
            self._provider_health[provider_name] = ProviderHealth()
            
        health = self._provider_health[provider_name]
        health.last_check = datetime.utcnow()
        
        try:
            is_ok = await check_fn()
            if is_ok:
                health.is_healthy = True
                health.consecutive_failures = 0
                return True
            else:
                health.consecutive_failures += 1
                if health.consecutive_failures >= 3:
                    health.is_healthy = False
                return False
        except Exception as e:
            logger.warning(f"Health check failed for {provider_name}: {e}")
            health.consecutive_failures += 1
            if health.consecutive_failures >= 3:
                health.is_healthy = False
            return False

    def is_healthy(self, provider_name: str) -> bool:
        if provider_name not in self._provider_health:
            return True # Assume healthy until checked
        return self._provider_health[provider_name].is_healthy

    def get_all_status(self) -> Dict[str, ProviderHealth]:
        return self._provider_health
