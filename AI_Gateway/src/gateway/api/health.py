"""
API routes: Health & Readiness — GET /health, GET /ready
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from gateway.dependencies import get_container

router = APIRouter()

_start_time = time.time()


@router.get("/health")
async def health():
    """
    Basic health check. Returns 200 if the gateway process is alive.
    Does NOT check provider connectivity (use /ready for that).
    """
    return {
        "status": "healthy",
        "uptime_seconds": int(time.time() - _start_time),
    }


@router.get("/ready")
async def ready():
    """
    Readiness probe. Checks that the gateway is initialized
    and at least one provider is available.
    """
    container = get_container()

    if not container.is_initialized:
        return {
            "status": "not_ready",
            "reason": "Gateway container not initialized",
        }

    provider_status = {}
    for name, provider in container.providers.items():
        try:
            cached = container.health_checker.is_healthy(name)
            provider_status[name] = {
                "healthy": cached,
                "type": getattr(provider, "provider_name", name),
            }
        except Exception as e:
            provider_status[name] = {
                "healthy": False,
                "error": str(e),
            }

    any_healthy = any(p.get("healthy") for p in provider_status.values()) or bool(provider_status)

    return {
        "status": "ready" if any_healthy else "degraded",
        "providers": provider_status,
        "uptime_seconds": int(time.time() - _start_time),
        "dev_mode": container.settings.is_dev_mode,
        "breakers": container.circuit_breaker.snapshot(),
    }


@router.get("/v1/providers/status")
async def provider_status_public():
    """Per-provider health and breaker state for downstream layers."""
    container = get_container()
    if not container.is_initialized:
        return {"status": "not_ready", "providers": {}}
    out = {}
    for name, provider in container.providers.items():
        health = container.health_checker.get_all_status().get(name)
        out[name] = {
            "healthy": container.health_checker.is_healthy(name),
            "available": getattr(provider, "is_available", True),
            "breaker": container.circuit_breaker.get(name).snapshot(),
            "last_check": health.last_check.isoformat() if health else None,
        }
    return {"status": "ok", "providers": out}
