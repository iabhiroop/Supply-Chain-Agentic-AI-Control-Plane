"""Feature/Expectation: Twin MCP catalog without Postgres."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from twin.mcp import ALIASES, handle_jsonrpc, list_tools  # noqa: E402


def test_required_tools():
    """Feature: Twin MCP catalog
    Expectation: inventory, PO, news, graph.query, and vendor.material tools are listed.
    """
    names = {t["name"] for t in list_tools()}
    assert {
        "inventory.days_of_cover",
        "po.open_for_vendor",
        "news.search",
        "graph.query",
        "vendor.material",
    } <= names
    assert "hitl.request" not in names


def test_jsonrpc_unknown_method():
    """Feature: MCP JSON-RPC errors
    Expectation: An unknown method returns jsonrpc error code -32601.
    """
    body = handle_jsonrpc({"jsonrpc": "2.0", "id": 7, "method": "nope"})
    assert "error" in body
    assert body["id"] == 7
    assert body["error"]["code"] == -32601


def test_legacy_aliases():
    """Feature: MCP aliases
    Expectation: twin.open_pos maps to po.open_for_vendor.
    """
    assert ALIASES["twin.open_pos"] == "po.open_for_vendor"


def test_graph_query_needs_template_or_query():
    """Feature: graph.query validation
    Expectation: Calling graph.query with no template and no query returns an error payload.
    """
    from twin.mcp import call_tool

    out = call_tool("graph.query", {})
    assert out.get("error") is True
