"""Agent memory: prompt seed, turns, blackboard, experience, apply skip."""

from __future__ import annotations

import os
import sys
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.contracts import AgentResult, AgentStatus  # noqa: E402
from agents.execution.loop import pack_turn  # noqa: E402
from agents.execution.trace import reset_trace_id, set_trace_id  # noqa: E402
from agents.memory.store import (  # noqa: E402
    active_prompt,
    already_applied,
    ensure,
    get_facts,
    last_experience,
    put_fact,
    record_experience,
    run_memory,
)


def _db_ready() -> bool:
    try:
        ensure()
        return active_prompt("Orchestrator", "system", workflow="p2p") is not None
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(_db_ready(), "logistics_scm / agent_memory schema unavailable")
class MemoryTests(unittest.TestCase):
    def test_prompt_seed_loads(self):
        """Feature: Agent memory prompt seed
        Expectation: P2P Orchestrator and disruption Logistics_Agent charters load; warehouse Smart_Stock_Agent stays frozen.
        """
        orch = active_prompt("Orchestrator", "system", workflow="p2p")
        self.assertIsNotNone(orch)
        self.assertIn("System of Execution", orch["body"])
        risk = active_prompt("Supplier_Risk_Agent", "charter", workflow="disruption")
        self.assertEqual(risk["prompt_id"], "disruption-risk-charter-v1")
        p2p_log = active_prompt("Logistics_Agent", "charter", workflow="p2p")
        d_log = active_prompt("Logistics_Agent", "charter", workflow="disruption")
        self.assertEqual(p2p_log["prompt_id"], "p2p-logistics-charter-v1")
        self.assertEqual(d_log["prompt_id"], "disruption-logistics-charter-v1")
        frozen = active_prompt("Smart_Stock_Agent", "charter", workflow="warehouse")
        self.assertIsNone(frozen)

    def test_pack_turn_writes_a_turn(self):
        """Feature: Agent memory turns
        Expectation: pack_turn writes one Sourcing_Agent turn for the trace.
        """
        trace_id = f"mem-turn-{uuid.uuid4().hex[:10]}"
        token = set_trace_id(trace_id)
        try:
            result = AgentResult(
                status=AgentStatus.SUCCESS,
                summary="Seeded turn for memory tests.",
                findings=["ok"],
                verified=True,
                confidence=1.0,
            )
            pack_turn("Sourcing_Agent", result, tool_calls=1, workflow="p2p")
        finally:
            reset_trace_id(token)
        mem = run_memory(trace_id)
        self.assertEqual(len(mem["turns"]), 1)
        self.assertEqual(mem["turns"][0]["agent_name"], "Sourcing_Agent")
        self.assertEqual(mem["turns"][0]["prompt_id"], "p2p-sourcing-charter-v1")

    def test_blackboard_last_write_wins(self):
        """Feature: Agent blackboard
        Expectation: Last put_fact for a key wins.
        """
        trace_id = f"mem-lww-{uuid.uuid4().hex[:10]}"
        put_fact("short_plants", ["PL-A"], source_agent="Inventory_Agent", trace_id=trace_id)
        put_fact("short_plants", ["PL-A", "PL-B"], source_agent="Inventory_Agent", trace_id=trace_id)
        facts = get_facts(trace_id)
        self.assertEqual(facts["short_plants"]["value"], ["PL-A", "PL-B"])

    def test_experience_on_disruption_recommend(self):
        """Feature: Disruption experience
        Expectation: A disruption_recommend row is stored and listed on the trace.
        """
        trace_id = f"mem-xp-{uuid.uuid4().hex[:10]}"
        record_experience(
            flow="disruption_recommend",
            summary="Reallocate 40% of PO-1842 to VEND-XYZ.",
            outcome={"alternate": "VEND-XYZ", "reallocate": {"po_id": "PO-1842", "qty": 160}},
            vendor_id="VEND-ABC",
            material_id="MAT-SEMI-1",
            po_id="PO-1842",
            trace_id=trace_id,
        )
        row = last_experience(flow="disruption_recommend", vendor_id="VEND-ABC")
        self.assertIsNotNone(row)
        self.assertIn("PO-1842", row["summary"])
        mem = run_memory(trace_id)
        self.assertEqual(len(mem["experiences"]), 1)
        self.assertEqual(mem["experiences"][0]["flow"], "disruption_recommend")

    def test_apply_skipped_if_already_applied(self):
        """Feature: Disruption apply skip
        Expectation: already_applied is remembered so a second apply does not mutate again.
        """
        record_experience(
            flow="disruption_apply",
            summary="Moved 160 off PO-MEM-APPLY.",
            outcome={"moved_qty": 160},
            po_id="PO-MEM-APPLY",
            vendor_id="VEND-ABC",
        )
        prior = already_applied("PO-MEM-APPLY")
        self.assertIsNotNone(prior)
        self.assertEqual(prior["flow"], "disruption_apply")

        from agents.config.db import fetch_one
        from agents.disruption.apply import apply_reallocation

        row = fetch_one(
            "SELECT quantity, reallocatable_qty FROM twin_purchase_orders WHERE po_id = %s",
            ("PO-1842",),
        )
        if not row:
            return
        qty = int(row["quantity"])
        realloc = int(row["reallocatable_qty"])
        if realloc >= qty:
            return
        out = apply_reallocation({"reallocate": {"po_id": "PO-1842", "qty": realloc + 1}})
        self.assertTrue(out.get("already_applied"))
        self.assertFalse(out.get("applied"))


if __name__ == "__main__":
    unittest.main()
