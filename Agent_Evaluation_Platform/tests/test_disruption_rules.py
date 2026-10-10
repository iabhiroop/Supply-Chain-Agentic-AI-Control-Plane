"""Disruption recommendation rule scorers — no database required."""

from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from eval_platform.scorers.disruption import score_run  # noqa: E402
from eval_platform.scorers.rules import case_passed  # noqa: E402

DATA = os.path.join(ROOT, "eval_platform", "data")


def _load(name: str) -> dict:
    with open(os.path.join(DATA, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


class DisruptionRuleTests(unittest.TestCase):
    def test_golden_abc_passes(self):
        """Feature: Disruption ABC golden
        Expectation: The ABC fixture passes hard_blockers, reallocate, and grounded cover.
        """
        scores = score_run(_load("disruption_abc.json"), {"workflow": "disruption"})
        self.assertTrue(case_passed(scores, {"workflow": "disruption"}))
        by = {s["metric"]: s for s in scores}
        self.assertTrue(by["hard_blockers"]["passed"])
        self.assertTrue(by["reallocate"]["passed"])
        self.assertTrue(by["cover_pct_grounded"]["passed"])

    def test_wrong_vendor_fails(self):
        """Feature: Disruption illegal vendor
        Expectation: Wrong-vendor fixture fails illegal_vendor and hard_blockers.
        """
        scores = score_run(_load("neg_disruption_wrong_vendor.json"), {"expect_fail": True})
        by = {s["metric"]: s for s in scores}
        self.assertFalse(by["illegal_vendor"]["passed"])
        self.assertFalse(by["hard_blockers"]["passed"])
        self.assertTrue(case_passed(scores, {"expect_fail": True}))

    def test_invented_sixty_percent_fails(self):
        """Feature: Disruption grounded cover
        Expectation: Invented 60% cover fails cover_pct_grounded.
        """
        scores = score_run(_load("neg_disruption_invented_cover.json"), {"expect_fail": True})
        by = {s["metric"]: s for s in scores}
        self.assertFalse(by["cover_pct_grounded"]["passed"])
        self.assertFalse(by["hard_blockers"]["passed"])
        self.assertTrue(case_passed(scores, {"expect_fail": True}))


if __name__ == "__main__":
    unittest.main()
