"""Grounded Taiwan chat through the AI Gateway. Numbers come from the JSON, not the model."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from agents.config.settings import settings

TAIWAN_QUESTION = (
    "A major supplier in Taiwan has announced a production shutdown. "
    "Which open purchase orders are at risk, and what should procurement do?"
)

_SYSTEM = (
    "You are the Disruption Orchestrator. Paraphrase the JSON recommendation only. "
    "Do not invent numbers, plants, POs, lead times, premiums, or cover percentages. "
    "If a figure is missing from the JSON, omit it."
)


def taiwan_chat(
    *,
    recommendation: dict[str, Any],
    trace_id: str,
    agent_id: str = "Procurement_Agent",
    question: str | None = None,
    semantic_cache: bool = True,
) -> dict[str, Any]:
    """POST /v1/chat/completions with the typed recommendation in the prompt.

    Omits `model` so the Gateway default_route / X-Route-Hint picks it.
    Semantic cache is opt-in on this narration path only.
    """
    key = settings.require_gateway_key()
    try:
        from agents.memory.store import active_body

        system = active_body("Disruption_Narrator", "system", workflow="disruption") or _SYSTEM
    except Exception:  # noqa: BLE001
        system = _SYSTEM
    asked = question or TAIWAN_QUESTION
    body = {
        "temperature": 0,
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"{asked}\n\nRecommendation JSON:\n"
                    f"{json.dumps(recommendation, default=str)}"
                ),
            },
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "X-Tenant-Id": settings.tenant_id,
        "X-Trace-Id": trace_id,
        "X-Agent-Id": agent_id,
        "X-Route-Hint": "cheap",
    }
    if semantic_cache:
        headers["X-Semantic-Cache"] = "on"
    req = urllib.request.Request(
        f"{settings.gateway_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            hdr_hit = (resp.headers.get("x-cache-hit") or "").lower() in {"1", "true", "yes"}
            cache_status = (resp.headers.get("x-cache-status") or "").lower() or (
                "hit" if hdr_hit else "miss"
            )
            budget_remaining = resp.headers.get("x-budget-remaining-usd")
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        raise RuntimeError(f"Gateway chat HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Gateway unreachable at {settings.gateway_url}: {exc}") from exc

    choice = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    usage = payload.get("usage") or {}
    return {
        "text": choice,
        "model": payload.get("model") or payload.get("x_model_used") or settings.model_cheap,
        "cache_hit": bool(payload.get("x_cache_hit")) or hdr_hit or cache_status in {"hit", "semantic_hit"},
        "cache_status": payload.get("x_cache_status") or cache_status,
        "budget_remaining_usd": budget_remaining,
        "cost_usd": float(payload.get("x_cost_usd") or 0),
        "latency_ms": payload.get("x_latency_ms"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "raw": payload,
    }


def _gateway_get(url: str, key: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        raise RuntimeError(f"Gateway GET {url} HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Gateway GET {url} failed: {exc}") from exc


def gateway_usage(trace_id: str) -> dict[str, Any]:
    key = settings.require_gateway_key()
    qs = urllib.parse.urlencode({"trace_id": trace_id})
    return _gateway_get(f"{settings.gateway_url.rstrip('/')}/v1/usage?{qs}", key)


def gateway_budget(agent_id: str | None = None) -> dict[str, Any]:
    key = settings.require_gateway_key()
    url = f"{settings.gateway_url.rstrip('/')}/v1/budget"
    if agent_id:
        url = f"{url}?{urllib.parse.urlencode({'agent_id': agent_id})}"
    return _gateway_get(url, key)
