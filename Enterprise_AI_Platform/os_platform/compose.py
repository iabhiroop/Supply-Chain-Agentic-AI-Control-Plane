"""Compose Taiwan ask: Layer 2 disruption + optional GraphRAG, Gateway chat, eval."""

from __future__ import annotations

import uuid
from typing import Any

from os_platform.config import settings
from os_platform.http import request

TAIWAN_QUESTION = (
    "A major supplier in Taiwan has announced a production shutdown. "
    "Which open purchase orders are at risk, and what should procurement do?"
)

PR1001 = "aaaaaaaa-bbbb-cccc-dddd-000000000001"


def _headers(trace_id: str, tenant_id: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    hdrs = {
        "X-Trace-Id": trace_id,
        "X-Tenant-Id": tenant_id,
    }
    if extra:
        hdrs.update(extra)
    return hdrs


def _classify(question: str) -> str:
    q = (question or "").lower()
    if "pr-1001" in q or "run p2p" in q or "golden transaction" in q:
        return "p2p"
    if "graph" in q and "only" in q:
        return "graphrag"
    return "disruption"


def stack_health() -> dict[str, Any]:
    checks = {
        "gateway": f"{settings.gateway_url.rstrip('/')}/health",
        "layer2": f"{settings.layer2_url.rstrip('/')}/health",
        "eval": f"{settings.eval_url.rstrip('/')}/health",
        "twin": f"{settings.twin_url.rstrip('/')}/health",
        "graphrag": f"{settings.graphrag_url.rstrip('/')}/health",
    }
    out = {}
    for name, url in checks.items():
        code, body = request("GET", url, timeout=2.0)
        out[name] = {"ok": 200 <= code < 300, "status": code, "body": body if code else {"error": body}}
    out["os"] = {"ok": True, "status": 200, "body": {"port": settings.port}}
    out["links"] = specialist_links()
    return out


def fetch_usage(trace_id: str, tenant_id: str) -> dict[str, Any] | None:
    url = f"{settings.gateway_url.rstrip('/')}/v1/usage?trace_id={trace_id}"
    hdrs = _headers(trace_id, tenant_id)
    if settings.gateway_api_key:
        hdrs["Authorization"] = f"Bearer {settings.gateway_api_key}"
    code, body = request("GET", url, headers=hdrs, timeout=5.0)
    if code == 200:
        return body
    return {"unavailable": True, "status": code, "detail": body}


def fetch_eval(trace_id: str) -> dict[str, Any] | None:
    code, body = request("GET", f"{settings.eval_url.rstrip('/')}/v1/traces/{trace_id}", timeout=5.0)
    if code == 200:
        return body
    return None


def score_disruption(trace_id: str, recommendation: dict, run_id: str | None) -> dict[str, Any] | None:
    code, body = request(
        "POST",
        f"{settings.eval_url.rstrip('/')}/v1/evaluations/score-disruption",
        body={
            "recommendation": recommendation,
            "trace_id": trace_id,
            "run_id": run_id,
            "expected": {"workflow": "disruption"},
        },
        timeout=8.0,
    )
    if code == 200:
        return {
            "trace_id": body.get("trace_id"),
            "run_id": body.get("run_id"),
            "scores": body.get("scores"),
        }
    return None


def fetch_scorecard(trace_id: str | None = None) -> dict[str, Any] | None:
    qs = f"?trace_id={trace_id}" if trace_id else ""
    code, body = request("GET", f"{settings.eval_url.rstrip('/')}/v1/scorecard{qs}", timeout=5.0)
    if code == 200:
        return body
    return None


def fetch_budget() -> dict[str, Any] | None:
    url = f"{settings.gateway_url.rstrip('/')}/v1/budget"
    hdrs = {}
    if settings.gateway_api_key:
        hdrs["Authorization"] = f"Bearer {settings.gateway_api_key}"
    code, body = request("GET", url, headers=hdrs, timeout=5.0)
    if code == 200:
        return body
    return {"unavailable": True, "status": code, "detail": body}


def fetch_mcp_tools() -> dict[str, Any]:
    code, body = request("GET", f"{settings.twin_url.rstrip('/')}/v1/mcp/tools", timeout=5.0)
    if code == 200 and isinstance(body, dict):
        names = [t.get("name") for t in (body.get("tools") or []) if t.get("name")]
        return {"ok": True, "tools": names, "catalog": body.get("tools")}
    return {"ok": False, "status": code, "detail": body, "tools": []}


def specialist_links() -> dict[str, str]:
    return {
        "os": f"http://{settings.host}:{settings.port}/",
        "gateway": f"{settings.gateway_url.rstrip('/')}/admin",
        "layer2": f"{settings.layer2_url.rstrip('/')}/dashboard",
        "eval": f"{settings.eval_url.rstrip('/')}/explorer",
        "twin": f"{settings.twin_url.rstrip('/')}/",
        "graphrag": f"{settings.graphrag_url.rstrip('/')}/search",
    }


def _hitl_row(tx: dict[str, Any]) -> bool:
    if tx.get("kind") == "model" or tx.get("request_id"):
        return True
    return tx.get("status") in {"AWAITING_HUMAN", "EXCEPTION"} or tx.get("stage") in {
        "AWAITING_HUMAN",
        "PO_PENDING_APPROVAL",
        "EXCEPTION",
    }


def fetch_inbox() -> dict[str, Any]:
    code, body = request(
        "GET",
        f"{settings.layer2_url.rstrip('/')}/v1/execution/overview",
        timeout=8.0,
    )
    if code != 200 or not isinstance(body, dict):
        return {"unavailable": True, "status": code, "detail": body, "waiting": [], "kpis": {}}
    waiting = body.get("waiting")
    if not isinstance(waiting, list):
        txs = body.get("transactions") or []
        waiting = [t for t in txs if _hitl_row(t)]
    return {**body, "waiting": waiting, "unavailable": False}


def twin_get(path: str) -> dict[str, Any]:
    code, body = request("GET", f"{settings.twin_url.rstrip('/')}{path}", timeout=5.0)
    if code == 200 and isinstance(body, dict):
        return body
    return {"unavailable": True, "status": code, "detail": body}


def fetch_twin_snapshot() -> dict[str, Any]:
    return {
        "plants_at_risk": twin_get("/v1/twin/plants-at-risk"),
        "days_of_cover": twin_get("/v1/twin/inventory/days-of-cover?material=MAT-SEMI-1"),
        "open_pos": twin_get("/v1/twin/pos/open?vendor=VEND-ABC"),
        "news": twin_get("/v1/twin/signals/news?entity=VEND-ABC"),
        "mcp": fetch_mcp_tools(),
    }


def apply_disruption(recommendation: dict[str, Any], decision: str = "approve") -> dict[str, Any]:
    code, body = request(
        "POST",
        f"{settings.layer2_url.rstrip('/')}/v1/disruption/apply",
        body={"recommendation": recommendation, "decision": decision},
        timeout=30.0,
    )
    if isinstance(body, dict):
        body.setdefault("status_code", code)
        return body
    return {"error": True, "status_code": code, "detail": body}


def undo_disruption(action_id: str) -> dict[str, Any]:
    code, body = request(
        "POST",
        f"{settings.layer2_url.rstrip('/')}/v1/disruption/undo",
        body={"action_id": action_id},
        timeout=30.0,
    )
    if isinstance(body, dict):
        body.setdefault("status_code", code)
        return body
    return {"error": True, "status_code": code, "detail": body}


def retrieve_citations(question: str, trace_id: str, tenant_id: str) -> dict[str, Any] | None:
    code, body = request(
        "POST",
        f"{settings.graphrag_url.rstrip('/')}/v1/answer",
        body={"query": question or TAIWAN_QUESTION, "k": 8},
        headers=_headers(trace_id, tenant_id),
        timeout=30.0,
    )
    if code == 200:
        return body
    code2, body2 = request(
        "POST",
        f"{settings.graphrag_url.rstrip('/')}/v1/retrieve",
        body={"query": question or TAIWAN_QUESTION, "k": 8},
        headers=_headers(trace_id, tenant_id),
        timeout=20.0,
    )
    if code2 == 200:
        return body2
    return {"unavailable": True, "status": code, "detail": body}


def run_p2p(transaction_id: str, trace_id: str, tenant_id: str) -> dict[str, Any]:
    code, body = request(
        "POST",
        f"{settings.layer2_url.rstrip('/')}/v1/execution/transactions/{transaction_id}/run",
        body={},
        headers=_headers(trace_id, tenant_id),
        timeout=180.0,
    )
    return {"status_code": code, "result": body}


def ask(
    *,
    question: str,
    tenant_id: str | None = None,
    trace_id: str | None = None,
    include_chat: bool = True,
    include_graphrag: bool = True,
    include_eval: bool = True,
) -> dict[str, Any]:
    tenant_id = tenant_id or settings.tenant_id
    trace_id = trace_id or f"os-{uuid.uuid4().hex[:16]}"
    kind = _classify(question)
    if kind == "p2p":
        p2p = run_p2p(PR1001, trace_id, tenant_id)
        return {
            "kind": "p2p",
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "question": question,
            "p2p": p2p,
            "usage": fetch_usage(trace_id, tenant_id),
            "eval": fetch_eval(trace_id),
        }
    if kind == "graphrag":
        citations = retrieve_citations(question, trace_id, tenant_id)
        return {
            "kind": "graphrag",
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "question": question,
            "citations": citations,
        }

    code, disruption = request(
        "POST",
        f"{settings.layer2_url.rstrip('/')}/v1/disruption/run",
        body={"vendor_id": "VEND-ABC", "material_id": "MAT-SEMI-1", "alternate_vendor_id": "VEND-XYZ"},
        headers=_headers(trace_id, tenant_id),
        timeout=60.0,
    )
    if code != 200 or not isinstance(disruption, dict):
        return {
            "kind": "disruption",
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "question": question,
            "error": disruption,
            "status_code": code,
        }
    # Prefer the Layer 2 minted id so Gateway usage and eval join.
    trace_id = disruption.get("trace_id") or trace_id
    rec = disruption.get("recommendation") or {}

    citations = None
    if include_graphrag:
        citations = retrieve_citations(
            question or "Which plants and purchase orders are affected if supplier ABC in Taiwan shuts down?",
            trace_id,
            tenant_id,
        )

    chat = None
    if include_chat and rec:
        ncode, narrate = request(
            "POST",
            f"{settings.layer2_url.rstrip('/')}/v1/disruption/narrate",
            body={"recommendation": rec, "trace_id": trace_id, "agent_id": "Procurement_Agent"},
            headers=_headers(trace_id, tenant_id),
            timeout=90.0,
        )
        chat = narrate if ncode == 200 else {"unavailable": True, "status": ncode, "detail": narrate}

    scored = None
    if include_eval and rec:
        scored = score_disruption(trace_id, rec, disruption.get("run_id"))

    mcp = fetch_mcp_tools()
    planned = (citations or {}).get("planned_query") or (citations or {}).get("retrieval", {}).get("planned_query") or {}
    template = (citations or {}).get("graph_template") or planned.get("template")
    usage = fetch_usage(trace_id, tenant_id)
    budget = fetch_budget()
    card = fetch_scorecard(trace_id) if include_eval else None

    return {
        "kind": "disruption",
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "question": question or TAIWAN_QUESTION,
        "recommendation": rec,
        "agent_results": disruption.get("agent_results") or {},
        "completed_agents": disruption.get("completed_agents") or [],
        "tool_calls": disruption.get("tool_calls"),
        "status": disruption.get("status"),
        "action_id": disruption.get("action_id"),
        "narrative": chat,
        "citations": citations,
        "eval": scored,
        "usage": usage,
        "tools": {
            "surface": "mcp",
            "catalog": mcp.get("tools") or [],
            "used": [
                "news.search",
                "inventory.days_of_cover",
                "po.open_for_vendor",
                "graph.query",
                "vendor.material",
            ],
            "runtime": "langgraph",
            "note": "Production disruption is LangGraph over twin HTTP. Bake-off runtime=mcp calls this MCP door.",
        },
        "graphrag": {
            "template": template,
            "planned_query": planned,
        },
        "gateway": {
            "cache_status": (chat or {}).get("cache_status"),
            "cache_hit": (chat or {}).get("cache_hit"),
            "budget": budget,
            "route_hint": "cheap",
        },
        "scorecard": card,
    }
