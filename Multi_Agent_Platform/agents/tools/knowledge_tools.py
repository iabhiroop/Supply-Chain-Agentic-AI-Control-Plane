"""GraphRAG tools. Agents never query Neo4j or the graphrag schema directly."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.config.settings import settings
from agents.execution.trace import current_trace_id

TIMEOUT = 20


def _graphrag(path: str, body: dict) -> Any:
    url = f"{settings.graphrag_url.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json", "X-Tenant-Id": settings.tenant_id}
    tid = current_trace_id()
    if tid:
        headers["X-Trace-Id"] = tid
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": True, "status_code": exc.code, "detail": exc.read().decode()[:400]}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"error": True, "detail": f"GraphRAG unreachable at {url}: {exc}"}


def knowledge_context(query: str) -> str | None:
    """Best-effort cited snippet for specialist findings. Never raises."""
    payload = _graphrag("/v1/retrieve", {"query": query, "k": 4})
    if payload.get("error"):
        return None
    bits = []
    for p in (payload.get("passages") or [])[:3]:
        bits.append(f"{p.get('citation')}: {(p.get('text') or '')[:220]}")
    for p in (payload.get("graph_paths") or [])[:4]:
        bits.append(p.get("path") or "")
    return " | ".join(bits) if bits else None


class QueryIn(BaseModel):
    query: str = Field(description="Natural-language knowledge question (SLA, contract, supplier risk, policy)")


@tool("knowledge_retrieve", args_schema=QueryIn)
def knowledge_retrieve(query: str):
    """Ranked passages and graph traversal paths from GraphRAG. Use for SLA, contracts, prior incidents. Citations included; no generation."""
    return _graphrag("/v1/retrieve", {"query": query, "k": 6})


@tool("knowledge_answer", args_schema=QueryIn)
def knowledge_answer(query: str):
    """Cited GraphRAG answer. Prefer this for customer-facing SLA wording. If abstained, do not invent facts."""
    return _graphrag("/v1/answer", {"query": query, "k": 6})


class KeyIn(BaseModel):
    key: str = Field(description="Vendor, material, order, or plant business key (e.g. VEND-A, MAT-11)")


@tool("knowledge_similar_incidents", args_schema=KeyIn)
def knowledge_similar_incidents(key: str):
    """Prior incidents and decisions from GraphRAG/Neo4j for this supplier, material, or plant. Use before awarding or rebooking."""
    return _graphrag("/v1/graph/query", {"template": "similar_incidents", "key": key})


def similar_incident_rows(key: str) -> list[dict]:
    payload = _graphrag("/v1/graph/query", {"template": "similar_incidents", "key": key})
    if payload.get("error"):
        return []
    return list(payload.get("incidents") or [])


def similar_incident_context(key: str) -> str | None:
    bits = []
    for inc in similar_incident_rows(key):
        bits.append(
            f"{inc.get('incident_key') or inc.get('incident')}: "
            f"{inc.get('recovery') or inc.get('decision') or inc.get('action') or ''} "
            f"slip={inc.get('slip_days')} effective={inc.get('effective')}"
        )
    return "; ".join(b.strip() for b in bits if b.strip()) or None
