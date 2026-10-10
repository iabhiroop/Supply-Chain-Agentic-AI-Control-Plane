"""Thin MCP tool door over twin reads + GraphRAG templates (no free Cypher).

JSON-RPC 2.0 at POST /mcp (tools/list, tools/call) and REST at /v1/mcp/*.
Agents must not invent SQL or Cypher; these names are the allowed surface.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from twin.config import settings
from twin.queries import days_of_cover, news_signals, open_pos, vendor_material

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "inventory.days_of_cover",
        "description": "Inventory positions and days of cover for a material.",
        "inputSchema": {
            "type": "object",
            "properties": {"material_id": {"type": "string", "default": "MAT-SEMI-1"}},
        },
    },
    {
        "name": "po.open_for_vendor",
        "description": "Open purchase orders, optionally filtered by vendor_id.",
        "inputSchema": {
            "type": "object",
            "properties": {"vendor_id": {"type": "string"}},
        },
    },
    {
        "name": "news.search",
        "description": "News signals from the twin, optionally filtered by entity_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "vendor_id": {"type": "string"},
            },
        },
    },
    {
        "name": "graph.query",
        "description": (
            "GraphRAG templated query only (neighbors, risk_if_slip, path, entity, "
            "similar_incidents, open_pos_at_plant). Natural-language `query` is planned "
            "to a template; free Cypher is rejected."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "template": {"type": "string"},
                "key": {"type": "string"},
                "src": {"type": "string"},
                "dst": {"type": "string"},
                "hops": {"type": "integer", "default": 2},
            },
        },
    },
    {
        "name": "vendor.material",
        "description": "Vendor–material commercial row (price, lead, capacity).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vendor_id": {"type": "string"},
                "material_id": {"type": "string", "default": "MAT-SEMI-1"},
            },
            "required": ["vendor_id"],
        },
    },
]

ALIASES = {
    "twin.news_signals": "news.search",
    "twin.inventory_days_of_cover": "inventory.days_of_cover",
    "twin.open_pos": "po.open_for_vendor",
    "twin.vendor_material": "vendor.material",
    "graphrag.knowledge_context": "graph.query",
}


def list_tools() -> list[dict[str, Any]]:
    return list(TOOL_DEFS)


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    arguments = dict(arguments or {})
    name = ALIASES.get(name, name)
    if name == "inventory.days_of_cover":
        return days_of_cover(arguments.get("material_id") or "MAT-SEMI-1")
    if name == "po.open_for_vendor":
        return open_pos(vendor_id=arguments.get("vendor_id") or None)
    if name == "news.search":
        entity = arguments.get("entity_id") or arguments.get("vendor_id")
        return news_signals(entity_id=entity)
    if name == "vendor.material":
        return vendor_material(
            arguments.get("vendor_id") or "",
            arguments.get("material_id") or "MAT-SEMI-1",
        )
    if name == "graph.query":
        return _graph_query(arguments)
    raise KeyError(f"Unknown MCP tool {name}")


def _graph_query(arguments: dict[str, Any]) -> dict[str, Any]:
    query = (arguments.get("query") or "").strip()
    template = (arguments.get("template") or "").strip()
    key = arguments.get("key")
    src = arguments.get("src")
    dst = arguments.get("dst")
    hops = int(arguments.get("hops") or 2)
    url = settings.graphrag_url.rstrip("/")
    if query and not template:
        plan = _post_json(f"{url}/v1/graph/plan", {"query": query})
        if plan.get("error") or plan.get("unavailable"):
            return plan
        template = plan.get("template") or ""
        key = key or plan.get("key")
        src = src or plan.get("src")
        dst = dst or plan.get("dst")
        hops = int(plan.get("hops") or hops)
        body = _post_json(
            f"{url}/v1/graph/query",
            {"template": template, "key": key, "src": src, "dst": dst, "hops": hops},
        )
        if isinstance(body, dict):
            body["planned_query"] = plan
        return body
    if not template:
        return {"error": True, "detail": "graph.query needs template or query"}
    return _post_json(
        f"{url}/v1/graph/query",
        {"template": template, "key": key, "src": src, "dst": dst, "hops": hops},
    )


def _post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return {"error": True, "status_code": exc.code, "detail": exc.read().decode()[:400]}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"unavailable": True, "error": True, "detail": str(exc), "url": url}


def handle_jsonrpc(payload: dict[str, Any]) -> dict[str, Any]:
    req_id = payload.get("id")
    method = payload.get("method") or ""
    params = payload.get("params") or {}
    try:
        if method in {"tools/list", "list_tools"}:
            result = {"tools": list_tools()}
        elif method in {"tools/call", "call_tool"}:
            name = params.get("name") or params.get("tool")
            args = params.get("arguments") or params.get("args") or {}
            result = {"content": [{"type": "json", "data": call_tool(name, args)}]}
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown method {method}"},
            }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except KeyError as exc:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": str(exc)}}
    except Exception as exc:  # noqa: BLE001
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(exc)}}
