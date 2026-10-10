"""Anchor extraction for twin + P2P keys."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from graphrag.ontology import extract_entity_keys  # noqa: E402
from graphrag.retrieve import classify_intent, extract_anchors  # noqa: E402


class AnchorTests(unittest.TestCase):
    def test_vend_abc_and_po(self):
        """Feature: Ontology entity keys
        Expectation: VEND-ABC, PO-1842, PL-IN-CHN, and MAT-SEMI-1 are extracted from a slip question.
        """
        keys = extract_entity_keys("If VEND-ABC slips, is PO-1842 at PL-IN-CHN at risk for MAT-SEMI-1?")
        self.assertIn("VEND-ABC", keys)
        self.assertIn("PO-1842", keys)
        self.assertIn("PL-IN-CHN", keys)
        self.assertIn("MAT-SEMI-1", keys)

    def test_shutdown_query_anchors(self):
        """Feature: Shutdown query anchors
        Expectation: A VEND-ABC shutdown question is multihop and includes MAT-SEMI-1.
        """
        q = "Which plants and purchase orders are exposed if VEND-ABC has a two-week shutdown?"
        self.assertEqual(classify_intent(q), "multihop")
        anchors = extract_anchors(q)
        self.assertIn("VEND-ABC", anchors)
        self.assertIn("MAT-SEMI-1", anchors)

    def test_p2p_vend_a_still_works(self):
        """Feature: P2P and site aliases
        Expectation: VEND-A and Le Havre (WH-FR-01) still resolve as anchors.
        """
        self.assertIn("VEND-A", extract_anchors("Which open orders if VEND-A slips two weeks?"))
        self.assertIn("WH-FR-01", extract_anchors("risk at the Le Havre DC"))


if __name__ == "__main__":
    unittest.main()
