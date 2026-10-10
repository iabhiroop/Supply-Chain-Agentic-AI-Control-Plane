"""Main Request Router."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from gateway.config import ModelsConfig, RoutesConfig
from gateway.models.key import VirtualKey
from gateway.models.request import ChatCompletionRequest
from gateway.router.ab_router import ABRouter
from gateway.router.cost_router import CostOptimizedRouter
from gateway.router.static_router import StaticRouter
from gateway.router.sticky_router import StickySessionRouter
from gateway.router.strategy import RoutingDecision, RoutingStrategy
from gateway.router.usage_aware import UsageAwareRouter

logger = logging.getLogger(__name__)


class RequestRouter:
    """Orchestrates routing strategy selection based on rules and request."""

    def __init__(
        self,
        models_config: ModelsConfig,
        routes_config: RoutesConfig,
        limiter=None,
        breakers=None,
        health_checker=None,
        budget_manager=None,
        overflow_threshold_pct: float = 80.0,
    ):
        self.models_config = models_config
        self.routes_config = routes_config
        self.limiter = limiter
        self.breakers = breakers
        self.health_checker = health_checker
        self.budget_manager = budget_manager

        self.usage_aware = UsageAwareRouter(
            models_config,
            routes_config,
            limiter=limiter,
            breakers=breakers,
            health_checker=health_checker,
            budget_manager=budget_manager,
            overflow_threshold_pct=overflow_threshold_pct,
        )
        self.strategies: Dict[str, RoutingStrategy] = {
            "static": StaticRouter(models_config),
            "cost_optimized": CostOptimizedRouter(models_config),
            "cost_optimised": CostOptimizedRouter(models_config),
            "usage_aware": self.usage_aware,
            "fallback": self.usage_aware,
            "sticky_session": StickySessionRouter(models_config),
            "balanced": self.usage_aware,
        }
        self._refresh_ab_strategies()

    def _refresh_ab_strategies(self) -> None:
        for rule in self.routes_config.rules:
            if rule.get("strategy") not in ("weighted_split", "ab_test"):
                continue
            chain = rule.get("fallback_chain") or []
            if len(chain) < 2:
                continue
            weight = float(rule.get("weight_a", 0.5))
            self.strategies[rule.get("name", "ab_test")] = ABRouter(
                model_a=chain[0].get("model", ""),
                provider_a=chain[0].get("provider", ""),
                model_b=chain[1].get("model", ""),
                provider_b=chain[1].get("provider", ""),
                weight_a=weight,
            )

    def fallback_chain_for(self, provider: str, model: str) -> list[dict[str, Any]]:
        limits = self.models_config.get_model_limits(provider, model)
        chain = list(limits.get("fallback_chain") or [])
        for rule in self.routes_config.rules:
            if rule.get("strategy") != "fallback":
                continue
            hops = rule.get("fallback_chain") or []
            if hops and hops[0].get("provider") == provider:
                for hop in hops[1:]:
                    if hop not in chain:
                        chain.append(hop)
        return chain

    async def route(self, request: ChatCompletionRequest, key: Optional[VirtualKey] = None, **extra: Any) -> RoutingDecision:
        context = {"key": key, **extra}

        header_strategy = request.x_routing_strategy
        if header_strategy == "sticky_session" or (request.x_session_id and header_strategy is None and extra.get("prefer_sticky")):
            return await self.strategies["sticky_session"].route(request, context)

        if header_strategy and header_strategy in self.strategies:
            return await self.strategies[header_strategy].route(request, context)

        agent_tags = key.permissions.tags if key else []
        header_map = extra.get("headers") or {}
        for rule in self.routes_config.rules:
            if not rule.get("is_active", True):
                continue
            conditions = rule.get("conditions", {}) or {}
            rule_tags = conditions.get("agent_tags", [])
            header_conds = conditions.get("headers") or {}
            matched = False
            if rule_tags and set(rule_tags).intersection(set(agent_tags)):
                matched = True
            if header_conds:
                matched = all(
                    str(header_map.get(k.lower(), "")).lower() == str(v).lower()
                    for k, v in header_conds.items()
                )
            if not matched:
                continue

            strategy_name = rule.get("strategy", "static")
            if strategy_name in ("fallback", "usage_aware", "balanced"):
                return await self.usage_aware.route(request, context)
            if strategy_name in self.strategies and strategy_name not in ("static",):
                decision = await self.strategies[strategy_name].route(request, context)
                decision.reason = f"Matched rule: {rule.get('name')} ({decision.reason})"
                return decision
            target = rule.get("target", {})
            return RoutingDecision(
                provider=target.get("provider", "gemini"),
                model=target.get("model", request.model),
                strategy=strategy_name,
                reason=f"Matched rule: {rule.get('name')}",
            )

        if extra.get("force_usage_aware", True):
            return await self.usage_aware.route(request, context)

        default = self.routes_config.default_route
        if request.model:
            return await self.strategies["static"].route(request, context)
        return RoutingDecision(
            provider=default.get("provider", "gemini"),
            model=default.get("model", request.model),
            strategy="default",
            reason="Fallback to default route",
        )
