"""Usage-aware routing: skip saturated / open-circuit models and follow fallback chains."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from gateway.config import ModelsConfig, RoutesConfig
from gateway.models.key import VirtualKey
from gateway.models.request import ChatCompletionRequest
from gateway.router.strategy import RoutingDecision, RoutingStrategy

logger = logging.getLogger(__name__)


class UsageAwareRouter(RoutingStrategy):
    """Pick the first candidate that is healthy and under its provider TPM/RPM cap."""

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
        self.overflow_threshold_pct = overflow_threshold_pct

    async def route(self, request: ChatCompletionRequest, context: Dict[str, Any]) -> RoutingDecision:
        key: Optional[VirtualKey] = context.get("key")
        estimated_tokens = int(context.get("estimated_tokens") or 0)
        budget_usage_pct = float(context.get("budget_usage_pct") or 0.0)

        candidates = self._candidates(request, key)
        reasons: List[str] = []
        considered = [f"{p}/{m}" for p, m, _ in candidates]

        if budget_usage_pct >= 80.0:
            cheap = self.models_config.cheapest_chat_models()
            if cheap:
                candidates = [(p, m, "budget_downgrade") for p, m, _ in cheap] + candidates

        for provider, model, why in candidates:
            ok, skip_reason = await self._is_available(provider, model, estimated_tokens)
            if not ok:
                reasons.append(f"{provider}/{model}: {skip_reason}")
                continue
            reason_code = why
            if budget_usage_pct >= 80.0 and why == "budget_downgrade":
                reason_code = "overflow_budget"
            return RoutingDecision(
                provider=provider,
                model=model,
                strategy="usage_aware",
                reason=reason_code,
                candidates=considered,
                skipped=reasons,
            )

        fallback = self.routes_config.default_route
        return RoutingDecision(
            provider=fallback.get("provider", "gemini"),
            model=fallback.get("model", request.model),
            strategy="usage_aware",
            reason="all_candidates_saturated",
            candidates=considered,
            skipped=reasons,
        )

    def _candidates(
        self, request: ChatCompletionRequest, key: Optional[VirtualKey]
    ) -> List[Tuple[str, str, str]]:
        out: List[Tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(provider: str, model: str, why: str) -> None:
            pair = (provider, model)
            if pair in seen or not provider or not model:
                return
            seen.add(pair)
            out.append((provider, model, why))

        found = self.models_config.find_model(request.model)
        if found:
            add(found[0], request.model, "primary")
            limits = self.models_config.get_model_limits(found[0], request.model)
            for hop in limits.get("fallback_chain") or []:
                add(hop.get("provider", ""), hop.get("model", ""), "configured_chain")

        for rule in self.routes_config.rules:
            if rule.get("strategy") != "fallback":
                continue
            for hop in rule.get("fallback_chain") or []:
                add(hop.get("provider", ""), hop.get("model", ""), "yaml_fallback")

        default = self.routes_config.default_route
        add(default.get("provider", "gemini"), default.get("model", request.model), "default")
        return out

    async def _is_available(self, provider: str, model: str, estimated_tokens: int) -> tuple[bool, str]:
        if self.breakers and self.breakers.is_open(provider):
            return False, "circuit_open"
        if self.breakers and self.breakers.is_open(f"{provider}:{model}"):
            return False, "circuit_open"
        if self.health_checker and not self.health_checker.is_healthy(provider):
            return False, "unhealthy"

        limits = self.models_config.get_model_limits(provider, model)
        rpm_cap = limits["provider_rpm"]
        tpm_cap = limits["provider_tpm"]
        threshold = (limits["overflow_threshold_pct"] or self.overflow_threshold_pct) / 100.0
        if self.limiter is None or (not rpm_cap and not tpm_cap):
            return True, ""

        dim = f"model:{provider}:{model}"
        peek = await self.limiter.peek(dim, rpm_cap, tpm_cap)
        if rpm_cap and peek.current_rpm >= rpm_cap:
            return False, "provider_rpm"
        if tpm_cap and (peek.current_tpm + estimated_tokens) >= tpm_cap:
            return False, "provider_tpm"
        if peek.utilization >= threshold:
            return False, "tpm_overflow"
        return True, ""
