"""Feature/Expectation: Graph plan surface without FastAPI startup / Postgres."""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from graphrag.query_planner import plan_query  # noqa: E402


def test_plan_query_is_the_http_contract():
    """Feature: POST /v1/graph/plan contract
    Expectation: plan_query (the handler body) returns risk_if_slip and never emits Cypher.
    """
    out = plan_query("Which plants if VEND-ABC shuts down?")
    assert out["template"] == "risk_if_slip"
    assert out["key"] == "VEND-ABC"
    assert out.get("cypher") is None


@pytest.mark.skip(reason="POST /v1/retrieve needs Postgres + corpus; skip live LLM accuracy")
def test_retrieve_http():
    """Feature: Retrieve HTTP
    Expectation: POST /v1/retrieve returns ranked passages (not run in CI).
    """
    raise AssertionError("unreachable")
