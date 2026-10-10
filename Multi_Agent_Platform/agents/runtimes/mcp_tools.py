"""MCP-shaped twin/knowledge tools. Prefer Twin MCP HTTP; fall back to local functions."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from agents.config.settings import settings
from agents.tools import twin_tools
from agents.tools.knowledge_tools import knowledge_context

ToolFn = Callable[..., Any]

MCP_NAMES = {
    "inventory.days_of_cover": "inventory.days_of_cover",
    "po.open_for_vendor": "po.open_for_vendor",
    "news.search": "news.search",
    "graph.query": "graph.query",
    "vendor.material": "vendor.material",
    "twin.news_signals": "news.search",
    "twin.inventory_days_of_cover": "inventory.days_of_cover",
    "twin.open_pos": "po.open_for_vendor",
    "twin.vendor_material": "vendor.material",
    "graphrag.knowledge_context": "graph.query",
}

TOOLS: dict[str, ToolFn] = {
    "twin.news_signals": twin_tools.news_signals,
    "twin.inventory_days_of_cover": twin_tools.inventory_days_of_cover,
    "twin.open_pos": twin_tools.open_pos,
    "twin.vendor_material": twin_tools.vendor_material,
    "graphrag.knowledge_context": knowledge_context,
    "inventory.days_of_cover": twin_tools.inventory_days_of_cover,
    "po.open_for_vendor": twin_tools.open_pos,
    "news.search": twin_tools.news_signals,
    "vendor.material": twin_tools.vendor_material,
}

_http_ok: bool | None = None


def list_tools() -> list[str]:
    return sorted(set(MCP_NAMES) | set(TOOLS))


def _mcp_http(name: str, arguments: dict[str, Any]) -> Any | None:
    global _http_ok
    if _http_ok is False:
        return None
    url = f"{settings.twin_url.rstrip('/')}/v1/mcp/tools/call"
    req = urllib.request.Request(
        url,
        data=json.dumps({"name": name, "arguments": arguments}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            _http_ok = True
            body = json.loads(resp.read().decode() or "{}")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        _http_ok = False
        return None
    if isinstance(body, dict) and body.get("error"):
        return None
    if isinstance(body, dict) and "result" in body:
        return body["result"]
    return body


def _arguments(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    arguments = dict(kwargs)
    mapped = MCP_NAMES.get(name, name)
    if mapped in {"news.search", "po.open_for_vendor"} and args:
        key = "entity_id" if mapped == "news.search" else "vendor_id"
        arguments.setdefault(key, args[0])
        if mapped == "news.search":
            arguments.setdefault("vendor_id", args[0])
    if mapped == "inventory.days_of_cover" and args:
        arguments.setdefault("material_id", args[0])
    if mapped == "vendor.material" and args:
        arguments.setdefault("vendor_id", args[0])
        if len(args) > 1:
            arguments.setdefault("material_id", args[1])
    if mapped == "graph.query" and args:
        arguments.setdefault("query", args[0])
    return arguments


def call_tool(name: str, *args: Any, **kwargs: Any) -> Any:
    mapped = MCP_NAMES.get(name, name)
    arguments = _arguments(name, args, kwargs)
    remote = _mcp_http(mapped, arguments)
    if remote is not None:
        return remote
    fn = TOOLS.get(name) or TOOLS.get(mapped)
    if fn is None:
        raise KeyError(f"Unknown MCP tool {name}")
    if mapped == "graph.query":
        return fn(arguments.get("query") or "")
    if mapped == "inventory.days_of_cover":
        return fn(arguments.get("material_id") or "MAT-SEMI-1")
    if mapped == "po.open_for_vendor":
        return fn(arguments.get("vendor_id"))
    if mapped == "news.search":
        return fn(arguments.get("entity_id") or arguments.get("vendor_id"))
    if mapped == "vendor.material":
        return fn(arguments.get("vendor_id") or "", arguments.get("material_id") or "MAT-SEMI-1")
    return fn(*args, **kwargs)
