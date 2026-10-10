"""Feature/Expectation: NL → GraphRAG templates. No database."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from graphrag.query_planner import plan_query  # noqa: E402


def test_taiwan_shutdown_is_risk_if_slip():
    """Feature: NL plan risk_if_slip
    Expectation: A VEND-ABC shutdown question plans to risk_if_slip with key VEND-ABC and no Cypher.
    """
    plan = plan_query("Which plants and purchase orders are exposed if VEND-ABC shuts down?")
    assert plan["template"] == "risk_if_slip"
    assert plan["key"] == "VEND-ABC"
    assert plan["cypher"] is None


def test_similar_incidents():
    """Feature: NL plan similar_incidents
    Expectation: Similar-incident wording plans to similar_incidents for VEND-A.
    """
    plan = plan_query("Show similar incidents for VEND-A")
    assert plan["template"] == "similar_incidents"
    assert plan["key"] == "VEND-A"


def test_open_pos_at_plant():
    """Feature: NL plan open_pos_at_plant
    Expectation: Open POs at PL-IN-CHN plans to open_pos_at_plant.
    """
    plan = plan_query("Which open POs at plant PL-IN-CHN?")
    assert plan["template"] == "open_pos_at_plant"
    assert plan["key"] == "PL-IN-CHN"


def test_path():
    """Feature: NL plan path
    Expectation: path from VEND-ABC to PO-1842 plans a path template.
    """
    plan = plan_query("path from VEND-ABC to PO-1842")
    assert plan["template"] == "path"
    assert plan["src"] == "VEND-ABC"
    assert plan["dst"] == "PO-1842"


def test_entity():
    """Feature: NL plan entity
    Expectation: What is PO-1842 plans to entity.
    """
    plan = plan_query("What is PO-1842?")
    assert plan["template"] == "entity"
    assert plan["key"] == "PO-1842"
