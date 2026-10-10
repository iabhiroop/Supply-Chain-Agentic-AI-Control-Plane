"""Circuit Breaker for failing providers."""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Protects against cascading failures for a single backend."""

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time = 0.0

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        self._check_state()
        if self.state == CircuitState.OPEN:
            raise Exception(f"Circuit breaker is OPEN ({self.name})")
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _check_state(self) -> None:
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker %s transitioned to HALF_OPEN", self.name)

    def _on_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failures = 0
            logger.info("Circuit breaker %s transitioned to CLOSED", self.name)
        elif self.state == CircuitState.CLOSED:
            self.failures = 0

    def _on_failure(self) -> None:
        self.failures += 1
        self.last_failure_time = time.time()
        if self.state == CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning("Circuit breaker %s transitioned to OPEN", self.name)

    def is_open(self) -> bool:
        self._check_state()
        return self.state == CircuitState.OPEN

    def snapshot(self) -> dict:
        self._check_state()
        return {
            "name": self.name,
            "state": self.state.value,
            "failures": self.failures,
        }


class CircuitBreakerRegistry:
    """One breaker per provider (and optionally per model)."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=self.failure_threshold,
                recovery_timeout=self.recovery_timeout,
            )
        return self._breakers[name]

    def is_open(self, name: str) -> bool:
        return self.get(name).is_open()

    def snapshot(self) -> dict[str, dict]:
        return {name: br.snapshot() for name, br in self._breakers.items()}
