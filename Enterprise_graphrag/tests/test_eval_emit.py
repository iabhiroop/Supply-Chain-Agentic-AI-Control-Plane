import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from graphrag.eval_emit import _parent_workflow_span, emit_retrieval  # noqa: E402
from graphrag.trace import bind_trace_id, reset_trace_id  # noqa: E402


class GraphragEmitTests(unittest.TestCase):
    def test_parent_span_from_layer2_trace_id(self):
        """Feature: GraphRAG parent workflow span
        Expectation: An l2-{txid}-{8hex} trace maps to wf-{txid}.
        """
        tid = "l2-aaaaaaaa-bbbb-cccc-dddd-000000000001-deadbeef"
        self.assertEqual(
            _parent_workflow_span(tid),
            "wf-aaaaaaaa-bbbb-cccc-dddd-000000000001",
        )

    def test_emit_reuses_bound_trace_id(self):
        """Feature: GraphRAG eval emit
        Expectation: emit_retrieval reuses the bound Layer 2 trace_id and parent span.
        """
        token = bind_trace_id("l2-aaaaaaaa-bbbb-cccc-dddd-000000000001-abcd1234")
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(req, timeout=2):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            return FakeResp()

        try:
            with patch("graphrag.eval_emit.urllib.request.urlopen", fake_urlopen):
                emit_retrieval("retrieve", "similar delays", {"passages": [1, 2], "intent": "incident"})
        finally:
            reset_trace_id(token)

        self.assertEqual(
            captured["body"]["trace_id"],
            "l2-aaaaaaaa-bbbb-cccc-dddd-000000000001-abcd1234",
        )
        self.assertEqual(
            captured["body"]["spans"][0]["parent_span_id"],
            "wf-aaaaaaaa-bbbb-cccc-dddd-000000000001",
        )
        self.assertEqual(captured["body"]["workflow"], "p2p_execution")


if __name__ == "__main__":
    unittest.main()
