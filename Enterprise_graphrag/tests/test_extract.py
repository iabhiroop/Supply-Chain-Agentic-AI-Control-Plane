"""Ontology-constrained LLM extraction for graph ingest."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from graphrag.extract import (  # noqa: E402
    apply_extraction,
    extract_from_text,
    iter_windows,
    parse_extraction,
    sanitize,
)


class ExtractSanitizeTests(unittest.TestCase):
    def test_parse_json_fence(self):
        """Feature: Extraction JSON fence
        Expectation: Fenced JSON yields VEND-ABC as a Supplier entity.
        """
        raw = '```json\n{"entities":[{"key":"VEND-ABC","label":"Supplier"}],"relations":[]}\n```'
        payload = parse_extraction(raw)
        self.assertEqual(payload["entities"][0]["key"], "VEND-ABC")

    def test_drops_off_ontology_and_ungrounded(self):
        """Feature: Extraction ontology filter
        Expectation: Off-ontology labels, ungrounded keys, and illegal rels are dropped.
        """
        text = "Contract for VEND-ABC supplying MAT-SEMI-1 to PL-IN-CHN."
        cleaned = sanitize(
            {
                "entities": [
                    {"key": "VEND-ABC", "label": "Supplier", "name": "ABC"},
                    {"key": "FAKE-99", "label": "Supplier", "name": "ghost"},
                    {"key": "MAT-SEMI-1", "label": "Spaceship", "name": "chip"},
                    {"key": "PL-IN-CHN", "label": "Site", "name": "Chennai"},
                ],
                "relations": [
                    {"src": "VEND-ABC", "rel": "SUPPLIES", "dst": "MAT-SEMI-1"},
                    {"src": "VEND-ABC", "rel": "TELEPORTS", "dst": "PL-IN-CHN"},
                    {"src": "VEND-ABC", "rel": "SUPPLIES", "dst": "FAKE-99"},
                ],
            },
            text,
        )
        keys = {e["key"] for e in cleaned["entities"]}
        self.assertEqual(keys, {"VEND-ABC", "PL-IN-CHN"})
        self.assertEqual(cleaned["relations"], [{"src": "VEND-ABC", "rel": "SUPPLIES", "dst": "MAT-SEMI-1"}])

    def test_extract_from_text_uses_chat_and_sanitizes(self):
        os.environ.pop("GRAPHRAG_EXTRACT_ONLY", None)
        os.environ["GRAPHRAG_LLM_INGEST"] = "true"
        text = "VEND-ABC SUPPLIES MAT-SEMI-1. Plant PL-IN-CHN USES MAT-SEMI-1. PO-1842 is open."

        def fake_chat(_system, _user):
            return (
                '{"entities":['
                '{"key":"VEND-ABC","label":"Supplier","name":"ABC"},'
                '{"key":"MAT-SEMI-1","label":"Material","name":"SEMI-X"},'
                '{"key":"MADE-UP","label":"Supplier","name":"nope"}'
                '],"relations":[{"src":"VEND-ABC","rel":"SUPPLIES","dst":"MAT-SEMI-1"}]}'
            )

        out = extract_from_text(text, chat_fn=fake_chat)
        self.assertFalse(out["skipped"])
        keys = {e["key"] for e in out["entities"]}
        self.assertIn("VEND-ABC", keys)
        self.assertNotIn("MADE-UP", keys)
        self.assertEqual(out["relations"][0]["rel"], "SUPPLIES")

    def test_skips_when_extract_only(self):
        os.environ["GRAPHRAG_EXTRACT_ONLY"] = "true"
        try:
            out = extract_from_text("VEND-ABC", chat_fn=lambda *_: "should not run")
            self.assertTrue(out["skipped"])
        finally:
            os.environ.pop("GRAPHRAG_EXTRACT_ONLY", None)

    def test_windows_extract_head_and_tail(self):
        os.environ.pop("GRAPHRAG_EXTRACT_ONLY", None)
        os.environ["GRAPHRAG_LLM_INGEST"] = "true"
        text = "VEND-ABC supplies MAT-SEMI-1. " + ("padding " * 900) + " Plant PL-IN-CHN uses MAT-SEMI-1."
        wins = iter_windows(text)
        self.assertGreaterEqual(len(wins), 2)
        self.assertIn("VEND-ABC", wins[0])
        self.assertIn("PL-IN-CHN", wins[-1])

        def fake_chat(_system, user):
            if "PL-IN-CHN" in user:
                return '{"entities":[{"key":"PL-IN-CHN","label":"Site","name":"Chennai"}],"relations":[]}'
            return (
                '{"entities":[{"key":"VEND-ABC","label":"Supplier","name":"ABC"},'
                '{"key":"MAT-SEMI-1","label":"Material","name":"SEMI-X"},'
                '{"key":"MADE-UP","label":"Supplier","name":"nope"}],'
                '"relations":[{"src":"VEND-ABC","rel":"SUPPLIES","dst":"MAT-SEMI-1"}]}'
            )

        keys = set()
        rels = []
        for win in wins:
            out = extract_from_text(win, chat_fn=fake_chat)
            self.assertFalse(out["skipped"])
            keys.update(e["key"] for e in out.get("entities") or [])
            rels.extend(out.get("relations") or [])
        self.assertIn("VEND-ABC", keys)
        self.assertIn("PL-IN-CHN", keys)
        self.assertNotIn("MADE-UP", keys)
        self.assertTrue(any(r["rel"] == "SUPPLIES" for r in rels))


class ExtractApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from graphrag.apply_schema import apply
            from graphrag.db import fetch_one

            apply()
            fetch_one("SELECT 1 AS ok")
            cls.ready = True
        except Exception:  # noqa: BLE001
            cls.ready = False

    def test_apply_writes_supplies_edge(self):
        if not self.ready:
            self.skipTest("graphrag schema / postgres unavailable")
        from graphrag.db import fetch_one
        from graphrag.graph_store import merge_node

        doc = merge_node("Document", "doc-llm-test", "llm test")
        text = "VEND-ABC supplies MAT-SEMI-1."
        counts = apply_extraction(
            {
                "entities": [
                    {"key": "VEND-ABC", "label": "Supplier", "name": "ABC"},
                    {"key": "MAT-SEMI-1", "label": "Material", "name": "SEMI-X"},
                ],
                "relations": [{"src": "VEND-ABC", "rel": "SUPPLIES", "dst": "MAT-SEMI-1"}],
            },
            document_id="doc-llm-test",
            doc_node_id=doc,
            source_text=text,
        )
        self.assertGreaterEqual(counts["relations"], 1)
        row = fetch_one(
            """
            SELECT e.rel_type
              FROM edges e
              JOIN nodes s ON s.node_id = e.src_id
              JOIN nodes d ON d.node_id = e.dst_id
             WHERE s.key = 'VEND-ABC' AND d.key = 'MAT-SEMI-1' AND e.rel_type = 'SUPPLIES'
             LIMIT 1
            """
        )
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
