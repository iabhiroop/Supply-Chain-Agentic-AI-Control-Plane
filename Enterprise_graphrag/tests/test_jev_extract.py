"""Jev candidate-judge extract for scm-v1 graph ingest."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from graphrag.jev_extract import (  # noqa: E402
    build_questions,
    extract_from_text,
    find_candidates,
    interpret_response,
    rel_options,
)


def _choice(choice: str, confidence: float = 0.9) -> SimpleNamespace:
    return SimpleNamespace(choice=choice, confidence=confidence, probabilities={choice: 1.0})


def _noul(p: float) -> SimpleNamespace:
    return SimpleNamespace(noul=p)


class CandidateTests(unittest.TestCase):
    def test_finds_ontology_keys_and_contract_id(self):
        text = "Contract C-VEND-A-2026: VEND-A supplies MAT-11 to WH-FR-01."
        keys = {c["key"] for c in find_candidates(text)}
        self.assertIn("VEND-A", keys)
        self.assertIn("MAT-11", keys)
        self.assertIn("WH-FR-01", keys)
        self.assertIn("C-VEND-A-2026", keys)

    def test_supplier_material_has_supplies(self):
        self.assertIn("SUPPLIES", rel_options("Supplier", "Material"))
        self.assertEqual(rel_options("Material", "Supplier"), [])


class InterpretTests(unittest.TestCase):
    def test_builds_label_and_pair_questions(self):
        window = "VEND-ABC supplies MAT-SEMI-1."
        cands = find_candidates(window)
        questions, meta = build_questions(window, cands)
        kinds = {m["kind"] for m in meta.values()}
        self.assertIn("label", kinds)
        self.assertIn("rel", kinds)
        self.assertIn("stated", kinds)
        self.assertTrue(questions)

    def test_interpret_keeps_grounded_supplies(self):
        window = "VEND-ABC supplies MAT-SEMI-1."
        cands = find_candidates(window)
        questions, meta = build_questions(window, cands)
        answers = {}
        for qid, info in meta.items():
            if info["kind"] == "label":
                label = "Supplier" if info["key"].startswith("VEND") else "Material"
                answers[qid] = _choice(label, 0.95)
            elif info["kind"] == "rel":
                if info["src"].startswith("VEND") and info["dst"].startswith("MAT"):
                    answers[qid] = _choice("SUPPLIES", 0.92)
                else:
                    answers[qid] = _choice("none", 0.8)
            else:
                p = 0.9 if info["src"].startswith("VEND") and info["dst"].startswith("MAT") else 0.1
                answers[qid] = _noul(p)
        response = SimpleNamespace(answers=answers, model="jev-fake", usage=None)
        out = interpret_response(response, candidates=cands, meta=meta, window=window)
        keys = {e["key"] for e in out["entities"]}
        self.assertEqual(keys, {"VEND-ABC", "MAT-SEMI-1"})
        self.assertEqual(out["relations"][0]["rel"], "SUPPLIES")

    def test_drops_low_confidence_and_unstated(self):
        window = "VEND-ABC and MAT-SEMI-1 appear in a table of contents."
        cands = find_candidates(window)
        _, meta = build_questions(window, cands)
        answers = {}
        for qid, info in meta.items():
            if info["kind"] == "label":
                answers[qid] = _choice("Supplier" if "VEND" in info["key"] else "Material", 0.2)
            elif info["kind"] == "rel":
                answers[qid] = _choice("SUPPLIES", 0.9)
            else:
                answers[qid] = _noul(0.1)
        out = interpret_response(
            SimpleNamespace(answers=answers, model="jev-fake", usage=None),
            candidates=cands,
            meta=meta,
            window=window,
        )
        self.assertEqual(out["entities"], [])
        self.assertEqual(out["relations"], [])

    def test_extract_from_text_uses_injected_client(self):
        os.environ.pop("GRAPHRAG_EXTRACT_ONLY", None)
        os.environ["GRAPHRAG_JEV_INGEST"] = "true"
        text = "VEND-ABC supplies MAT-SEMI-1."

        def fake_system_one(state, questions):
            answers = {}
            for qid in questions:
                if qid.startswith("label__VEND"):
                    answers[qid] = _choice("Supplier")
                elif qid.startswith("label__MAT"):
                    answers[qid] = _choice("Material")
                elif qid.startswith("rel__VEND") and "MAT" in qid:
                    answers[qid] = _choice("SUPPLIES")
                elif qid.startswith("stated__VEND") and "MAT" in qid:
                    answers[qid] = _noul(0.95)
                elif qid.startswith("rel__"):
                    answers[qid] = _choice("none")
                else:
                    answers[qid] = _noul(0.05)
            return SimpleNamespace(answers=answers, model="jev-fake", usage=None)

        out = extract_from_text(text, system_one_fn=fake_system_one)
        self.assertFalse(out["skipped"])
        self.assertIn("VEND-ABC", {e["key"] for e in out["entities"]})
        self.assertTrue(any(r["rel"] == "SUPPLIES" for r in out["relations"]))


if __name__ == "__main__":
    unittest.main()
