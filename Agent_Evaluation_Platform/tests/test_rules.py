"""Deterministic P2P rule scorers — no database required."""

from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from eval_platform.scorers.rules import case_passed, score_run  # noqa: E402

DATA = os.path.join(ROOT, "eval_platform", "data")


def _load(name: str) -> dict:
    with open(os.path.join(DATA, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


class RuleScorerTests(unittest.TestCase):
    def test_vend_c_win_is_hard_blocker(self):
        """Feature: P2P illegal vendor rule
        Expectation: A VEND-C win fixture is a hard blocker; expect_fail still passes the case.
        """
        scores = score_run(_load("neg_vend_c_win.json"), {"expect_fail": True})
        by = {s["metric"]: s for s in scores}
        self.assertFalse(by["business_rule"]["passed"])
        self.assertFalse(by["hard_blockers"]["passed"])
        self.assertTrue(case_passed(scores, {"expect_fail": True}))

    def test_freight_over_ceiling_is_hard_blocker(self):
        """Feature: P2P freight ceiling rule
        Expectation: Freight over ceiling is a hard blocker.
        """
        scores = score_run(_load("neg_freight_ceiling.json"), {"expect_fail": True})
        by = {s["metric"]: s for s in scores}
        self.assertFalse(by["business_rule_freight"]["passed"])
        self.assertFalse(by["hard_blockers"]["passed"])
        self.assertTrue(case_passed(scores, {"expect_fail": True}))

    def test_legal_po_is_not_a_blocker(self):
        """Feature: P2P legal SETTLED snapshot
        Expectation: A legal VEND-A SETTLED snap is not a hard blocker.
        """
        snap = {
            "transaction": {"stage": "SETTLED", "status": "SETTLED", "context": {}},
            "material": {"last_price": 10},
            "policy": {"price_deviation_pct": 5},
            "quotes": [{"vendor_id": "VEND-C", "unit_price": 11.2}],
            "po": {"vendor_id": "VEND-A", "unit_price": 10.2, "quantity": 20},
            "booking": {"freight_cost": 85.0, "status": "BOOKED"},
            "gr": {"quantity": 20},
            "match": {"status": "POSTED"},
            "actions": [
                {"agent_name": "Sourcing_Agent", "action_type": "ISSUE_RFQ"},
                {"agent_name": "Procurement_Agent", "action_type": "CREATE_PO"},
            ],
            "agent_results": {"Procurement_Agent": {"status": "OK", "summary": "Awarded VEND-A."}},
        }
        expected = {
            "stage": "SETTLED",
            "po_vendor": "VEND-A",
            "vend_c_quote": True,
            "match_posted": True,
            "not_hitl": True,
        }
        scores = score_run(snap, expected)
        self.assertTrue(case_passed(scores, expected))

    def test_finance_create_po_fails_trajectory(self):
        """Feature: P2P tool trajectory
        Expectation: Finance_Agent CREATE_PO fails tool_trajectory.
        """
        snap = {
            "transaction": {"stage": "PO_ISSUED", "status": "OPEN"},
            "agent_results": {"Finance_Agent": {"status": "OK"}},
            "actions": [{"agent_name": "Finance_Agent", "action_type": "CREATE_PO"}],
        }
        scores = score_run(snap, {})
        by = {s["metric"]: s for s in scores}
        self.assertFalse(by["tool_trajectory"]["passed"])
        self.assertFalse(by["hard_blockers"]["passed"])


if __name__ == "__main__":
    unittest.main()
