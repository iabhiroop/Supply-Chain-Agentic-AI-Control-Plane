"""
Dependency Injection container.

Creates and wires together all gateway components.
In dev mode, uses in-memory stores. In prod, uses Redis + PostgreSQL.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from gateway.config import GatewaySettings, ModelsConfig, RoutesConfig, get_settings

logger = logging.getLogger(__name__)


class GatewayContainer:
    """
    Central DI container. Holds singleton instances of all gateway services.
    Initialized once at app startup.
    """

    def __init__(self, settings: GatewaySettings | None = None):
        self.settings = settings or get_settings()
        self._initialized = False

        self.models_config: ModelsConfig | None = None
        self.routes_config: RoutesConfig | None = None

        self.key_manager = None
        self.request_validator = None
        self.cache = None
        self.rate_limiter = None
        self.budget_manager = None
        self.router = None
        self.health_checker = None
        self.retry_engine = None
        self.circuit_breaker = None
        self.token_counter = None
        self.cost_calculator = None
        self.request_logger = None
        self.live_usage = None
        self.catalog = None
        self.alert_manager = None
        self._health_task = None

        self.providers: dict = {}

    async def initialize(self) -> None:
        """Initialize all services. Call this once at app startup."""
        if self._initialized:
            return

        logger.info(
            "Initializing gateway container (dev_mode=%s)",
            self.settings.is_dev_mode,
        )

        self.models_config = ModelsConfig(self.settings.models_config_path)
        self.routes_config = RoutesConfig(self.settings.routes_config_path)

        from gateway.db.repository import DatabaseRepository
        self.db_repo = None
        if self.settings.database_url:
            try:
                self.db_repo = DatabaseRepository(self.settings.database_url)
                await self.db_repo.initialize_schema()
            except Exception as exc:
                logger.warning(
                    "Primary database unavailable (%s); falling back to SQLite",
                    exc,
                )
                self.db_repo = DatabaseRepository("sqlite+aiosqlite:///./gateway.db")
                await self.db_repo.initialize_schema()

        from gateway.auth.virtual_keys import KeyManager
        from gateway.validation.schema import RequestValidator

        self.key_manager = KeyManager(db=self.db_repo)
        self.request_validator = RequestValidator(self.models_config)

        if self.settings.has_redis:
            from gateway.cache.redis_cache import RedisExactMatchCache
            from gateway.ratelimit.redis_ratelimit import RedisRateLimiter
            redis_ok = False
            try:
                candidate = RedisRateLimiter(self.settings.redis_url)
                redis_ok = await candidate.ping()
            except Exception:
                redis_ok = False
                candidate = None
            if redis_ok:
                self.rate_limiter = candidate
                from gateway.cache.layered import LayeredCache
                from gateway.cache.redis_cache import RedisExactMatchCache
                self.cache = LayeredCache(RedisExactMatchCache(self.settings.redis_url))
            else:
                logger.warning("Redis ping failed; using in-memory cache/limiter")
                if candidate is not None:
                    try:
                        await candidate.close()
                    except Exception:
                        pass
                from gateway.cache.exact_cache import ExactMatchCache
                from gateway.cache.layered import LayeredCache
                from gateway.ratelimit.token_bucket import RateLimiter
                self.cache = LayeredCache(ExactMatchCache())
                self.rate_limiter = RateLimiter()
        else:
            from gateway.cache.exact_cache import ExactMatchCache
            from gateway.cache.layered import LayeredCache
            from gateway.ratelimit.token_bucket import RateLimiter
            self.cache = LayeredCache(ExactMatchCache())
            self.rate_limiter = RateLimiter()

        from gateway.budget.budget_gate import BudgetManager
        self.budget_manager = BudgetManager(db=self.db_repo)

        from gateway.metering.token_counter import TokenCounter
        from gateway.metering.cost_calculator import CostCalculator

        self.token_counter = TokenCounter()
        self.cost_calculator = CostCalculator(self.models_config)

        from gateway.observability.logger import RequestLogger
        from gateway.observability.metrics import MetricsExporter
        from gateway.observability.tracing import TracingManager
        from gateway.observability.live_usage import LiveUsageTracker

        self.request_logger = RequestLogger(db=self.db_repo)
        self.metrics_exporter = MetricsExporter()
        self.tracing_manager = TracingManager(otlp_endpoint=self.settings.otel_endpoint)
        self.live_usage = LiveUsageTracker(self.rate_limiter)

        from gateway.governance.agent_guard import AgentGuard
        self.agent_guard = AgentGuard()

        from gateway.prompt_registry.registry import PromptRegistry, PromptTemplate
        self.prompt_registry = PromptRegistry()
        self.prompt_registry.register_prompt(PromptTemplate(
            name="logistics-agent",
            version="1.0",
            system_prompt="You are a logistics routing expert.",
            user_template="Find the best route for {shipment_id}",
        ))

        from gateway.alerts.manager import AlertManager
        self.alert_manager = AlertManager()
        hooks = [u.strip() for u in (self.settings.alert_webhooks or "").split(",") if u.strip()]
        if self.db_repo:
            try:
                hooks.extend(await self.db_repo.list_alert_webhooks())
            except Exception:
                pass
        self.alert_manager.set_webhooks(hooks)

        from gateway.registry.catalog import CatalogRegistry
        self.catalog = CatalogRegistry(self.settings, self.models_config, db=self.db_repo)
        await self.catalog.hydrate(self.providers)

        from gateway.retry.retry_engine import RetryEngine
        from gateway.retry.circuit_breaker import CircuitBreakerRegistry
        from gateway.loadbalancer.health_check import HealthChecker

        self.health_checker = HealthChecker()
        self.retry_engine = RetryEngine()
        self.circuit_breaker = CircuitBreakerRegistry()

        from gateway.router.router import RequestRouter
        self.router = RequestRouter(
            models_config=self.models_config,
            routes_config=self.routes_config,
            limiter=self.rate_limiter,
            breakers=self.circuit_breaker,
            health_checker=self.health_checker,
            budget_manager=self.budget_manager,
            overflow_threshold_pct=self.settings.overflow_threshold_pct,
        )

        self._health_task = asyncio.create_task(self._health_loop())

        self._initialized = True
        logger.info("Gateway container initialized successfully")

    async def _health_loop(self) -> None:
        while True:
            try:
                for name, provider in list(self.providers.items()):
                    ok = await self.health_checker.check_provider(name, provider.health_check)
                    breaker = self.circuit_breaker.get(name)
                    if breaker.is_open():
                        await self.alert_manager.maybe_breaker(name, "open")
                    if not ok:
                        logger.warning("Provider %s failed health check", name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("Health loop error: %s", exc)
            await asyncio.sleep(30)

    async def shutdown(self) -> None:
        """Cleanup resources on shutdown."""
        logger.info("Shutting down gateway container")
        if self._health_task:
            self._health_task.cancel()
        if self.rate_limiter and hasattr(self.rate_limiter, "close"):
            try:
                await self.rate_limiter.close()
            except Exception:
                pass
        for name, provider in self.providers.items():
            try:
                await provider.close()
            except Exception as e:
                logger.warning("Error closing provider %s: %s", name, e)
        if self.db_repo:
            await self.db_repo.close()

    @property
    def is_initialized(self) -> bool:
        return self._initialized


_container: GatewayContainer | None = None


def get_container() -> GatewayContainer:
    """Get or create the global gateway container."""
    global _container
    if _container is None:
        _container = GatewayContainer()
    return _container


async def initialize_container(settings: GatewaySettings | None = None) -> GatewayContainer:
    """Initialize the global container. Call once at startup."""
    global _container
    _container = GatewayContainer(settings)
    await _container.initialize()
    return _container
