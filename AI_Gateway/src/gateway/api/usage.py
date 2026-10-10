"""
API routes: Usage & Cost Reporting — GET /v1/usage, GET /v1/budget, GET /v1/models
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, Query

from gateway.dependencies import get_container
from gateway.models.key import VirtualKey

router = APIRouter()


async def _require_virtual_key(authorization: str | None) -> VirtualKey | None:
    container = get_container()
    if not authorization:
        if container.settings.is_dev_mode:
            return None
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    raw_key = authorization.removeprefix("Bearer ").strip()
    key = await container.key_manager.validate_key(raw_key)
    if key is None:
        raise HTTPException(status_code=401, detail="Invalid or expired API key")
    return key


def _trace_of(record) -> str | None:
    meta = record.metadata or {}
    tid = meta.get("trace_id")
    return str(tid) if tid else None


@router.get("/v1/usage")
async def get_usage(
    period: str = Query("today", description="Time period: today, week, month"),
    trace_id: str | None = Query(None, description="Filter usage_records.extra_json.trace_id"),
    authorization: str | None = Header(None),
):
    """
    Get token and cost usage for the authenticated key.
    Returns aggregated usage for the requested period.
    Pass trace_id to join a Layer 2/3 workflow to per-request rows.
    """
    container = get_container()
    if not container.is_initialized:
        raise HTTPException(status_code=503, detail="Gateway not initialized")

    key = await _require_virtual_key(authorization)

    now = datetime.utcnow()
    if trace_id:
        start = now - timedelta(days=7)
    elif period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = now - timedelta(days=7)
    elif period == "month":
        start = now - timedelta(days=30)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    records = await container.request_logger.get_records(start)
    if key is not None:
        records = [r for r in records if r.key_id == key.id]
    if trace_id:
        records = [r for r in records if _trace_of(r) == trace_id]

    total_requests = len(records)
    total_input = sum(r.input_tokens for r in records)
    total_output = sum(r.output_tokens for r in records)
    total_cost = sum(r.cost_usd for r in records)
    total_cached = sum(1 for r in records if r.cache_hit)
    avg_latency = (
        sum(r.latency_ms for r in records) / total_requests
        if total_requests > 0
        else 0
    )

    model_breakdown = {}
    for r in records:
        label = f"{r.provider}/{r.model}"
        if label not in model_breakdown:
            model_breakdown[label] = {
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            }
        model_breakdown[label]["requests"] += 1
        model_breakdown[label]["input_tokens"] += r.input_tokens
        model_breakdown[label]["output_tokens"] += r.output_tokens
        model_breakdown[label]["cost_usd"] += r.cost_usd

    payload = {
        "period": "trace" if trace_id else period,
        "period_start": start.isoformat(),
        "period_end": now.isoformat(),
        "key_id": str(key.id) if key else None,
        "project_id": key.project_id if key else None,
        "trace_id": trace_id,
        "total_requests": total_requests,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "total_cost_usd": round(total_cost, 6),
        "cache_hit_count": total_cached,
        "cache_hit_rate": round(total_cached / total_requests, 4) if total_requests > 0 else 0,
        "avg_latency_ms": round(avg_latency, 1),
        "model_breakdown": model_breakdown,
    }
    if trace_id:
        payload["requests"] = [
            {
                "gateway_request_id": r.gateway_request_id,
                "model": r.model,
                "provider": r.provider,
                "agent_id": r.agent_id,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens": r.total_tokens,
                "cost_usd": round(r.cost_usd, 6),
                "cache_hit": bool(r.cache_hit),
                "latency_ms": r.latency_ms,
            }
            for r in records
        ]
    return payload


@router.get("/v1/budget")
async def get_budget(
    authorization: str | None = Header(None),
    agent_id: str | None = Query(None),
    x_agent_id: str | None = Header(None),
):
    """Get remaining budget for the authenticated key's project and named agents."""
    container = get_container()
    if not container.is_initialized:
        raise HTTPException(status_code=503, detail="Gateway not initialized")

    from gateway.governance.named_agents import NAMED_AGENT_LIMITS, scope_id as agent_scope

    key = await _require_virtual_key(authorization)
    scope_id = key.project_id if key else "default"
    limit = float(container.settings.default_daily_budget_usd or 0)
    await container.budget_manager.ensure_budget(scope_id, limit)

    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    records = await container.request_logger.get_records(start)
    if key is not None:
        records = [
            r for r in records
            if r.project_id == key.project_id or r.key_id == key.id
        ]
    spent_from_usage = sum(r.cost_usd for r in records)

    status = await container.budget_manager.get_status(scope_id)
    spent = status.spent_usd if status else 0.0
    if spent_from_usage > spent:
        spent = spent_from_usage
    remaining = max(0.0, limit - spent)
    usage_pct = round((spent / limit) * 100, 2) if limit else 0.0

    named_out = {}
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

    wanted = agent_id or x_agent_id
    if wanted and wanted in named_out:
        payload = dict(named_out[wanted])
        payload["project"] = {
            "scope": "project",
            "scope_id": scope_id,
            "limit_usd": limit,
            "spent_usd": round(spent, 6),
            "remaining_usd": round(remaining, 6),
            "usage_pct": usage_pct,
        }
        payload["named_agents"] = named_out
        return payload

    return {
        "scope": "project",
        "scope_id": scope_id,
        "period": "daily",
        "limit_usd": limit,
        "spent_usd": round(spent, 6),
        "remaining_usd": round(remaining, 6),
        "usage_pct": usage_pct,
        "hard_limit": True if status is None else status.hard_limit,
        "named_agents": named_out,
    }


@router.get("/v1/models")
async def list_models(
    authorization: str | None = Header(None),
):
    """
    List available models, filtered by key permissions.
    OpenAI-compatible format.
    """
    container = get_container()
    if not container.is_initialized:
        raise HTTPException(status_code=503, detail="Gateway not initialized")

    key = None
    if authorization:
        key = await _require_virtual_key(authorization)

    models = container.models_config.get_all_models_flat()

    data = []
    for m in models:
        if m.get("status") != "active":
            continue
        if key and not key.can_access_model(m["model"]):
            continue
        data.append({
            "id": m["model"],
            "object": "model",
            "created": 0,
            "owned_by": m["provider"],
            "capabilities": m.get("capabilities", []),
            "context_window": m.get("context_window", 0),
        })

    return {
        "object": "list",
        "data": data,
    }
