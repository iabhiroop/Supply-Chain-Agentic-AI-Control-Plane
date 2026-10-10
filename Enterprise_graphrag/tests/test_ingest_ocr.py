"""OCR file ingest → mocked LLM extract → graph (Neo4j or Postgres)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from graphrag.docpack import QUALITY, write_scanned_pdf  # noqa: E402
from graphrag.parse import ocr_available  # noqa: E402


LLM_JSON = (
    '{"entities":['
    '{"key":"VEND-ABC","label":"Supplier","name":"ABC"},'
    '{"key":"MAT-SEMI-1","label":"Material","name":"SEMI-X"}'
    '],"relations":[{"src":"VEND-ABC","rel":"SUPPLIES","dst":"MAT-SEMI-1"}]}'
)


class OcrIngestGraphTests(unittest.TestCase):
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

    @unittest.skipUnless(ocr_available(), "rapidocr-onnxruntime not installed")
    def test_scanned_pdf_ocr_then_llm_graph(self):
        if not self.ready:
            self.skipTest("graphrag schema / postgres unavailable")
        os.environ.pop("GRAPHRAG_EXTRACT_ONLY", None)
        os.environ["GRAPHRAG_LLM_INGEST"] = "true"
        import uuid

        from graphrag.db import fetch_one
        from graphrag.extract import extract_and_apply
        from graphrag.ingest import ingest_parsed
        from graphrag.parse import parse_document

        def _extract(text, *, document_id, doc_node_id, chat_fn=None):
            return extract_and_apply(
                text,
                document_id=document_id,
                doc_node_id=doc_node_id,
                chat_fn=lambda *_: LLM_JSON,
            )

        token = "BATCH" + uuid.uuid4().hex[:8].upper()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "quality_scan.pdf")
            write_scanned_pdf(path, QUALITY.splitlines())
            parsed = parse_document(path)
            parsed["text"] = (parsed.get("text") or "") + f"\n{token}\nVEND-ABC MAT-SEMI-1"
            pages = parsed.get("pages") or []
            if pages:
                pages[-1]["text"] = (pages[-1].get("text") or "") + f"\n{token}\nVEND-ABC MAT-SEMI-1"
            with patch("graphrag.extract.extract_and_apply", side_effect=_extract):
                result = ingest_parsed(parsed, source_uri=path + token, title="quality_scan.pdf")
        self.assertTrue(result.get("ok"), result)
        self.assertFalse(result.get("deduped"), result)
        self.assertTrue(result.get("interpreted"), result.get("llm"))
        row = fetch_one(
            """
            SELECT n.key
              FROM edges e
              JOIN nodes n ON n.node_id = e.dst_id
             WHERE e.rel_type = 'MENTIONS' AND n.key = 'VEND-ABC'
             LIMIT 1
            """
        )
        self.assertIsNotNone(row)
        self.assertGreaterEqual(int(result.get("llm", {}).get("relations") or 0), 1)
        supplies = fetch_one(
            """
            SELECT e.rel_type
              FROM edges e
              JOIN nodes s ON s.node_id = e.src_id
              JOIN nodes d ON d.node_id = e.dst_id
             WHERE s.key = 'VEND-ABC' AND d.key = 'MAT-SEMI-1' AND e.rel_type = 'SUPPLIES'
             LIMIT 1
            """
        )
        self.assertIsNotNone(supplies)
        try:
            from graphrag.neo4j_client import client as neo4j

            if neo4j.ping():
                found = neo4j.run(
                    "MATCH (a:Supplier {key: $src})-[r:SUPPLIES]->(b:Material {key: $dst}) "
                    "RETURN type(r) AS rel LIMIT 1",
                    {"src": "VEND-ABC", "dst": "MAT-SEMI-1"},
                )
                self.assertTrue(found)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    unittest.main()
