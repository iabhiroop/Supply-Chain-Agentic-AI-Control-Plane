"""Analytics rollups for the admin charts API."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable

from gateway.models.usage import UsageRecord

RANGE_WINDOWS = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return float(ordered[idx])


def _matches(record: UsageRecord, provider: str, model: str, project: str) -> bool:
    if provider and record.provider != provider:
        return False
    if model and record.model != model:
        return False
    if project and record.project_id != project:
        return False
    return True


def _bucket_key(dt: datetime, grain: str) -> str:
    if grain == "minute":
        return dt.strftime("%H:%M")
    if grain == "hour":
        return dt.strftime("%Y-%m-%d %H:00")
    return dt.strftime("%Y-%m-%d")


def _empty_point() -> dict[str, Any]:
    return {
        "requests": 0,
        "tokens": 0,
        "cost": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "fallbacks": 0,
        "rate_limit_hits": 0,
        "cache_hits": 0,
        "errors": 0,
        "latencies": [],
        "by_model": defaultdict(lambda: {"requests": 0, "tokens": 0, "cost": 0.0}),
    }


def rollup_records(
    records: Iterable[UsageRecord],
    *,
    grain: str,
    provider: str = "",
    model: str = "",
    project: str = "",
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        if not _matches(record, provider, model, project):
            continue
        key = _bucket_key(record.created_at, grain)
        point = buckets.setdefault(key, _empty_point())
        point["requests"] += 1
        point["tokens"] += record.total_tokens
        point["cost"] += record.cost_usd
        point["input_tokens"] += record.input_tokens
        point["output_tokens"] += record.output_tokens
        point["cached_tokens"] += record.cached_tokens
        point["reasoning_tokens"] += record.reasoning_tokens
        if record.fallback_used:
            point["fallbacks"] += 1
        if record.error_code == "RATE_LIMITED":
            point["rate_limit_hits"] += 1
        if record.cache_hit:
            point["cache_hits"] += 1
        if record.error_code:
            point["errors"] += 1
        if record.latency_ms:
            point["latencies"].append(record.latency_ms)
        label = f"{record.provider}/{record.model}" if record.provider else record.model
        stats = point["by_model"][label]
        stats["requests"] += 1
        stats["tokens"] += record.total_tokens
        stats["cost"] += record.cost_usd
    return buckets


def summarize_records(
    records: list[UsageRecord],
    *,
    provider: str = "",
    model: str = "",
    project: str = "",
    budget_limit: float = 0.0,
) -> dict[str, Any]:
    filtered = [r for r in records if _matches(r, provider, model, project)]
    cost_by_model: dict[str, float] = defaultdict(float)
    cost_by_provider: dict[str, float] = defaultdict(float)
    cost_by_project: dict[str, float] = defaultdict(float)
    cost_by_agent: dict[str, float] = defaultdict(float)
    requests_by_model: dict[str, int] = defaultdict(int)
    tokens_by_model: dict[str, int] = defaultdict(int)
    route_mix: dict[str, int] = defaultdict(int)
    errors_by_code: dict[str, int] = defaultdict(int)
    errors_by_provider: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "error": 0})
    latency_by_model: dict[str, list[float]] = defaultdict(list)
    cache_hits = 0
    fallbacks = 0
    avoided = 0.0
    input_tokens = output_tokens = cached_tokens = reasoning_tokens = 0
    total_cost = 0.0
    total_tokens = 0
    for record in filtered:
        label = f"{record.provider}/{record.model}" if record.provider else (record.model or "unknown")
        cost_by_model[label] += record.cost_usd
        cost_by_provider[record.provider or "unknown"] += record.cost_usd
        cost_by_project[record.project_id or "default"] += record.cost_usd
        if record.agent_id:
            cost_by_agent[record.agent_id] += record.cost_usd
        requests_by_model[label] += 1
        tokens_by_model[label] += record.total_tokens
        route_mix[record.route_strategy or "unknown"] += 1
        if record.error_code:
            errors_by_code[record.error_code] += 1
            errors_by_provider[record.provider or "unknown"]["error"] += 1
        else:
            errors_by_provider[record.provider or "unknown"]["ok"] += 1
        if record.latency_ms:
            latency_by_model[label].append(float(record.latency_ms))
        if record.cache_hit:
            cache_hits += 1
        if record.fallback_used:
            fallbacks += 1
        avoided += record.cache_cost_avoided_usd
        input_tokens += record.input_tokens
        output_tokens += record.output_tokens
        cached_tokens += record.cached_tokens
        reasoning_tokens += record.reasoning_tokens
        total_cost += record.cost_usd
        total_tokens += record.total_tokens

    latency_percentiles = {}
    for label, samples in latency_by_model.items():
        latency_percentiles[label] = {
            "p50": round(_percentile(samples, 50), 1),
            "p95": round(_percentile(samples, 95), 1),
            "count": len(samples),
        }

    efficiency = {}
    for label, tokens in tokens_by_model.items():
        cost = cost_by_model.get(label, 0)
        efficiency[label] = round(tokens / cost, 1) if cost > 0 else 0.0

    spent = total_cost
    remaining = max(0.0, budget_limit - spent) if budget_limit else None
    return {
        "totals": {
            "requests": len(filtered),
            "tokens": total_tokens,
            "cost_usd": round(total_cost, 6),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / len(filtered), 4) if filtered else 0,
            "fallbacks": fallbacks,
            "cache_cost_avoided_usd": round(avoided, 6),
            "budget_limit_usd": budget_limit,
            "budget_remaining_usd": remaining,
        },
        "tokens_by_type": {
            "input": input_tokens,
            "output": output_tokens,
            "cached": cached_tokens,
            "reasoning": reasoning_tokens,
        },
        "cost_by_model": {k: round(v, 6) for k, v in cost_by_model.items()},
        "cost_by_provider": {k: round(v, 6) for k, v in cost_by_provider.items()},
        "cost_by_project": {k: round(v, 6) for k, v in cost_by_project.items()},
        "cost_by_agent": {k: round(v, 6) for k, v in cost_by_agent.items()},
        "requests_by_model": dict(requests_by_model),
        "tokens_by_model": dict(tokens_by_model),
        "route_mix": dict(route_mix),
        "errors_by_code": dict(errors_by_code),
        "errors_by_provider": dict(errors_by_provider),
        "latency_percentiles": latency_percentiles,
        "efficiency_tokens_per_usd": efficiency,
    }


def series_from_buckets(buckets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    labels = sorted(buckets.keys())
    throughput = []
    tokens_by_type = []
    tokens_by_model_series: dict[str, list[float]] = defaultdict(list)
    fallbacks = []
    cache_hit_rate = []
    all_models: set[str] = set()
    for label in labels:
        point = buckets[label]
        all_models.update(point["by_model"].keys())
    for label in labels:
        point = buckets[label]
        req = point["requests"]
        throughput.append({
            "label": label,
            "rpm": point["requests"],
            "tpm": point["tokens"],
            "cost": round(point["cost"], 6),
            "fallbacks": point["fallbacks"],
            "rate_limit_hits": point["rate_limit_hits"],
        })
        tokens_by_type.append({
            "label": label,
            "input": point["input_tokens"],
            "output": point["output_tokens"],
            "cached": point["cached_tokens"],
            "reasoning": point["reasoning_tokens"],
        })
        fallbacks.append({
            "label": label,
            "fallbacks": point["fallbacks"],
            "rate_limit_hits": point["rate_limit_hits"],
            "errors": point["errors"],
        })
        cache_hit_rate.append({
            "label": label,
            "rate": round(point["cache_hits"] / req, 4) if req else 0,
        })
        for model_name in all_models:
            stats = point["by_model"].get(model_name) or {"tokens": 0}
            tokens_by_model_series[model_name].append(stats.get("tokens") or 0)
    latency_over_time = []
    for label in labels:
        samples = buckets[label]["latencies"]
        latency_over_time.append({
            "label": label,
            "p50": round(_percentile(samples, 50), 1),
            "p95": round(_percentile(samples, 95), 1),
        })
    return {
        "labels": labels,
        "throughput": throughput,
        "tokens_by_type": tokens_by_type,
        "tokens_by_model": {k: v for k, v in tokens_by_model_series.items()},
        "fallbacks": fallbacks,
        "cache_hit_rate": cache_hit_rate,
        "latency_over_time": latency_over_time,
    }


def from_live_sparkline(
    points: list[dict[str, Any]],
    *,
    provider: str = "",
    model: str = "",
    project: str = "",
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    needle = f"{provider}/{model}" if provider and model else (model or provider)
    for point in points:
        label = point.get("label") or str(point.get("minute"))
        by_model = point.get("by_model") or {}
        if needle:
            matched = {
                k: v for k, v in by_model.items()
                if (provider and k.startswith(provider + "/")) or (model and k.endswith("/" + model)) or k == needle
            }
            if provider and model:
                matched = {k: v for k, v in by_model.items() if k == needle}
            tokens = sum(v.get("tokens", 0) for v in matched.values())
            requests = sum(v.get("requests", 0) for v in matched.values())
            cost = sum(v.get("cost", 0) for v in matched.values())
        else:
            matched = by_model
            tokens = point.get("tokens") or 0
            requests = point.get("requests") or 0
            cost = point.get("cost") or 0
        if project:
            proj = (point.get("by_project") or {}).get(project) or {}
            tokens = proj.get("tokens") or 0
            requests = proj.get("requests") or 0
            cost = proj.get("cost") or 0
        entry = _empty_point()
        entry["requests"] = requests
        entry["tokens"] = tokens
        entry["cost"] = cost
        entry["input_tokens"] = point.get("input_tokens") or 0
        entry["output_tokens"] = point.get("output_tokens") or 0
        entry["cached_tokens"] = point.get("cached_tokens") or 0
        entry["reasoning_tokens"] = point.get("reasoning_tokens") or 0
        entry["fallbacks"] = point.get("fallbacks") or 0
        entry["rate_limit_hits"] = point.get("rate_limit_hits") or 0
        entry["cache_hits"] = point.get("cache_hits") or 0
        entry["errors"] = sum((point.get("errors") or {}).values())
        entry["latencies"] = list(point.get("latencies") or [])
        for k, v in matched.items():
            entry["by_model"][k] = v
        buckets[label] = entry
    return buckets


def grain_for_range(range_key: str) -> str:
    if range_key in ("15m", "1h"):
        return "minute"
    if range_key == "24h":
        return "hour"
    return "day"
