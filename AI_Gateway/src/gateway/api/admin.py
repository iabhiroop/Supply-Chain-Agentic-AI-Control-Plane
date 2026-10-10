"""
API routes: Admin functionality — keys, models, live usage, routes, alerts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel, Field

from gateway.dependencies import get_container
from gateway.models.key import KeyCreate, KeyResponse, VirtualKey

router = APIRouter()


def _require_admin(authorization: str | None) -> None:
    container = get_container()
    if container.settings.is_dev_mode:
        return
    if container.settings.debug and not container.settings.admin_key:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != container.settings.admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


class ModelCreate(BaseModel):
    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""
    key_name: str = ""
    context_window: int = 128000
    max_output_tokens: int = 8192
    capabilities: list[str] = Field(default_factory=lambda: ["chat"])
    input_per_1m_tokens: float = 0.0
    output_per_1m_tokens: float = 0.0
    status: str = "active"
    provider_rpm: int = 60
    provider_tpm: int = 100000
    overflow_threshold_pct: float = 80.0
    fallback_chain: list[dict[str, Any]] = Field(default_factory=list)


class ProviderKeyCreate(BaseModel):
    provider: str
    name: str = "default"
    api_key: str = ""
    base_url: str = ""


class ConnectionTest(BaseModel):
    provider: str
    api_key: str = ""
    base_url: str = ""


class RoutesUpdate(BaseModel):
    default_route: dict[str, Any] | None = None
    rules: list[dict[str, Any]] | None = None


class WebhookCreate(BaseModel):
    url: str


@router.get("/v1/admin/keys", response_model=List[VirtualKey])
async def list_keys(authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    return await container.key_manager.list_keys()


@router.post("/v1/admin/keys", response_model=KeyResponse)
async def create_key(request: KeyCreate, authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    raw_key, vk = await container.key_manager.create_key(request)
    return KeyResponse(
        id=vk.id,
        key=raw_key,
        key_prefix=vk.key_prefix,
        name=vk.name,
        organization_id=vk.organization_id,
        team_id=vk.team_id,
        project_id=vk.project_id,
        is_active=vk.is_active,
        created_at=vk.created_at,
        expires_at=vk.expires_at,
    )


@router.delete("/v1/admin/keys/{key_id}")
async def revoke_key(key_id: uuid.UUID, authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    success = await container.key_manager.revoke_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "revoked", "key_id": key_id}


@router.get("/v1/admin/dashboard")
async def get_dashboard_stats(authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    since = datetime.utcnow() - timedelta(days=30)
    records = await container.request_logger.get_records(since)
    total_cost = sum(r.cost_usd for r in records)
    total_requests = len(records)
    total_tokens = sum(r.total_tokens for r in records)
    model_costs: dict[str, float] = {}
    timeline: dict[str, dict[str, float]] = {}
    for r in records:
        model_costs[r.model] = model_costs.get(r.model, 0.0) + r.cost_usd
        day = r.created_at.strftime("%Y-%m-%d")
        bucket = timeline.setdefault(day, {"requests": 0, "cost": 0.0})
        bucket["requests"] += 1
        bucket["cost"] += r.cost_usd
    live = await container.live_usage.snapshot(container.models_config.get_all_models_flat())
    named = await _named_agent_budgets(container)
    return {
        "total_cost_usd": round(total_cost, 4),
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "model_costs": {k: round(v, 4) for k, v in model_costs.items()},
        "timeline": timeline,
        "live": live,
        "named_agents": named,
    }


async def _named_agent_budgets(container) -> dict[str, Any]:
    from gateway.governance.named_agents import NAMED_AGENT_LIMITS, scope_id as agent_scope

    named_out: dict[str, Any] = {}
    for name, spec in NAMED_AGENT_LIMITS.items():
        sid = agent_scope(name)
        await container.budget_manager.ensure_budget(sid, float(spec["daily_usd"]))
        st = await container.budget_manager.get_status(sid)
        named_out[name] = {
            "scope": "agent",
            "scope_id": sid,
            "rpm": spec["rpm"],
            "limit_usd": float(spec["daily_usd"]),
            "spent_usd": round(st.spent_usd if st else 0.0, 6),
            "remaining_usd": round(st.remaining_usd if st else float(spec["daily_usd"]), 6),
            "usage_pct": st.usage_pct if st else 0.0,
        }
    return named_out


@router.get("/v1/admin/cache")
async def get_cache_stats(authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    cache = container.cache
    store = dict(getattr(cache, "stats", {}) or {}) if cache is not None else {}
    since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    records = await container.request_logger.get_records(since)
    exact = semantic = miss = 0
    for r in records:
        status = str((r.metadata or {}).get("cache_status") or "")
        if status in {"semantic_hit", "semantic"}:
            semantic += 1
        elif r.cache_hit or status in {"hit", "exact"}:
            exact += 1
        else:
            miss += 1
    total = len(records)
    hits = exact + semantic
    return {
        "store": store,
        "today": {
            "requests": total,
            "exact_hits": exact,
            "semantic_hits": semantic,
            "misses": miss,
            "cache_hit_rate": round(hits / total, 4) if total else 0,
        },
        "named_agents": await _named_agent_budgets(container),
    }


@router.get("/v1/admin/analytics")
async def get_analytics(
    range: str = "24h",
    provider: str = "",
    model: str = "",
    project: str = "",
    authorization: str | None = Header(None),
):
    _require_admin(authorization)
    container = get_container()
    from gateway.observability.analytics import (
        RANGE_WINDOWS,
        from_live_sparkline,
        grain_for_range,
        rollup_records,
        series_from_buckets,
        summarize_records,
    )

    range_key = range if range in RANGE_WINDOWS else "24h"
    delta = RANGE_WINDOWS[range_key]
    since = datetime.utcnow() - delta
    records = await container.request_logger.get_records(since)
    summary = summarize_records(
        records,
        provider=provider,
        model=model,
        project=project,
        budget_limit=float(container.settings.default_daily_budget_usd or 0),
    )
    grain = grain_for_range(range_key)
    if range_key in ("15m", "1h"):
        minutes = 15 if range_key == "15m" else 60
        points = container.live_usage.sparkline(minutes)
        buckets = from_live_sparkline(points, provider=provider, model=model, project=project)
        if not any(p.get("requests") for p in buckets.values()) and records:
            buckets = rollup_records(
                records, grain="minute", provider=provider, model=model, project=project
            )
    else:
        buckets = rollup_records(
            records, grain=grain, provider=provider, model=model, project=project
        )
    series = series_from_buckets(buckets)
    live = await container.live_usage.snapshot(container.models_config.get_all_models_flat())
    quota_by_model = [
        {
            "label": f"{m['provider']}/{m['model']}",
            "tpm": m.get("tpm") or 0,
            "tpm_limit": m.get("tpm_limit") or 0,
            "rpm": m.get("rpm") or 0,
            "rpm_limit": m.get("rpm_limit") or 0,
            "utilization": m.get("utilization") or 0,
        }
        for m in live.get("models") or []
        if (not provider or m.get("provider") == provider) and (not model or m.get("model") == model)
    ]
    spent = summary["totals"]["cost_usd"]
    limit = summary["totals"]["budget_limit_usd"] or 0
    cumulative = []
    running = 0.0
    for point in series["throughput"]:
        running += point.get("cost") or 0
        cumulative.append({
            "label": point["label"],
            "spend": round(running, 6),
            "budget": limit,
            "remaining": max(0.0, limit - running) if limit else None,
        })
    return {
        "range": range_key,
        "grain": grain,
        "filters": {"provider": provider, "model": model, "project": project},
        "generated_at": datetime.utcnow().isoformat(),
        **summary,
        **series,
        "quota_by_model": quota_by_model,
        "cumulative_spend": cumulative,
        "in_flight": live.get("in_flight") or 0,
    }


@router.get("/v1/admin/live")
async def get_live_stats(authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    snap = await container.live_usage.snapshot(container.models_config.get_all_models_flat())
    snap["breakers"] = container.circuit_breaker.snapshot()
    snap["quota_pct"] = 0.0
    if snap.get("models"):
        utils = [m.get("utilization") or 0 for m in snap["models"]]
        snap["quota_pct"] = round(max(utils) * 100, 1) if utils else 0.0
    return snap


@router.get("/v1/admin/models")
async def list_models(authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    live = await container.live_usage.snapshot(container.models_config.get_all_models_flat())
    live_by = {(m["provider"], m["model"]): m for m in live.get("models", [])}
    keys = {k["provider"]: k for k in container.catalog.list_keys_public()}
    out = []
    for row in container.models_config.get_all_models_flat():
        stats = live_by.get((row["provider"], row["model"]), {})
        key = keys.get(row["provider"], {})
        limits = container.models_config.get_model_limits(row["provider"], row["model"])
        out.append({
            **row,
            "provider_rpm": limits["provider_rpm"],
            "provider_tpm": limits["provider_tpm"],
            "overflow_threshold_pct": limits["overflow_threshold_pct"],
            "fallback_chain": limits["fallback_chain"],
            "live_rpm": stats.get("rpm", 0),
            "live_tpm": stats.get("tpm", 0),
            "utilization": stats.get("utilization", 0),
            "key_last4": key.get("key_last4") or "",
        })
    return out


@router.post("/v1/admin/models")
async def create_model(payload: ModelCreate, authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    try:
        created = await container.catalog.add_model(payload.model_dump(), container.providers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return created


@router.patch("/v1/admin/models/{provider}/{model}")
async def update_model(
    provider: str,
    model: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    authorization: str | None = Header(None),
):
    _require_admin(authorization)
    container = get_container()
    payload = dict(payload)
    payload["provider"] = provider
    payload["model"] = model
    try:
        return await container.catalog.add_model(payload, container.providers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/v1/admin/models/{provider}/{model}")
async def delete_model(provider: str, model: str, authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    ok = await container.catalog.delete_model(provider, model)
    if not ok:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"status": "deleted", "provider": provider, "model": model}


@router.post("/v1/admin/provider-keys")
async def create_provider_key(payload: ProviderKeyCreate, authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    return await container.catalog.add_provider_key(
        payload.provider,
        name=payload.name,
        api_key=payload.api_key,
        base_url=payload.base_url,
        providers=container.providers,
    )


@router.get("/v1/admin/provider-keys")
async def list_provider_keys(authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    return container.catalog.list_keys_public()


@router.delete("/v1/admin/provider-keys/{key_id}")
async def delete_provider_key(key_id: uuid.UUID, authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    if not container.db_repo:
        raise HTTPException(status_code=400, detail="Provider keys require a database")
    ok = await container.db_repo.delete_provider_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found")
    await container.catalog.apply_credentials(container.providers)
    return {"status": "deleted", "key_id": str(key_id)}


@router.post("/v1/admin/models/test")
async def test_model_connection(payload: ConnectionTest, authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    return await container.catalog.test_connection(
        payload.provider, api_key=payload.api_key, base_url=payload.base_url
    )


@router.get("/v1/admin/routes")
async def get_routes(authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    return container.routes_config.as_dict()


@router.put("/v1/admin/routes")
async def put_routes(payload: RoutesUpdate, authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    data = container.routes_config.as_dict()
    if payload.default_route is not None:
        data["default_route"] = payload.default_route
    if payload.rules is not None:
        data["rules"] = payload.rules
    container.routes_config.set_data(data)
    try:
        container.routes_config.persist()
    except Exception:
        pass
    container.router._refresh_ab_strategies()
    return container.routes_config.as_dict()


@router.get("/v1/admin/alerts")
async def list_alerts(authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    return {
        "webhooks": container.alert_manager.webhooks,
        "history": container.alert_manager.history[:20],
    }


@router.post("/v1/admin/alerts/webhooks")
async def add_webhook(payload: WebhookCreate, authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    hooks = list(container.alert_manager.webhooks)
    if payload.url not in hooks:
        hooks.append(payload.url)
    container.alert_manager.set_webhooks(hooks)
    if container.db_repo:
        await container.db_repo.add_alert_webhook(payload.url)
    return {"webhooks": hooks}


@router.get("/v1/admin/providers")
async def admin_providers(authorization: str | None = Header(None)):
    _require_admin(authorization)
    container = get_container()
    health = {
        name: (status.model_dump() if hasattr(status, "model_dump") else status)
        for name, status in container.health_checker.get_all_status().items()
    }
    return {
        "providers": {
            name: {
                "configured": True,
                "available": getattr(p, "is_available", True),
            }
            for name, p in container.providers.items()
        },
        "health": health,
        "breakers": container.circuit_breaker.snapshot(),
    }
