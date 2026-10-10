"""Feature/Expectation: Disruption HITL apply without mutating ERP."""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.disruption.apply import apply_reallocation  # noqa: E402


def test_reject_does_not_need_database():
    """Feature: Disruption HITL reject
    Expectation: decision=reject returns applied False without touching Postgres.
    """
    out = apply_reallocation(
        {"reallocate": {"po_id": "PO-1842", "qty": 160}},
        decision="reject",
    )
    assert out["ok"] is True
    assert out["applied"] is False
    assert out["decision"] == "reject"
    assert out["po_id"] == "PO-1842"


def test_unknown_decision_is_error():
    """Feature: Disruption HITL unknown action
    Expectation: An unknown decision returns error without applying.
    """
    out = apply_reallocation({"reallocate": {"po_id": "PO-1842", "qty": 160}}, decision="yeet")
    assert out.get("error") is True


def test_missing_qty_is_error():
    """Feature: Disruption HITL empty rec
    Expectation: Approve with no reallocatable qty returns an error.
    """
    out = apply_reallocation({"reallocate": {}}, decision="approve")
    assert out.get("error") is True


@pytest.mark.skip(reason="needs logistics_scm / twin_purchase_orders (laptop ERP)")
def test_approve_mutates_po_1842():
    """Feature: Disruption HITL approve
    Expectation: Approve reallocates PO-1842 and returns action_id (ERP required).
    """
    raise AssertionError("unreachable")


@pytest.mark.skip(reason="needs logistics_scm execution snapshot (laptop ERP)")
def test_hitl_decide_resume():
    """Feature: P2P HITL decide/resume
    Expectation: POST decide on a held job resumes the graph (ERP required).
    """
    raise AssertionError("unreachable")
