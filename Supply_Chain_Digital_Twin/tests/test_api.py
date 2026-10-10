"""Smoke tests for twin read API (needs loaded disruption pack)."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from twin.main import app  # noqa: E402


def _pack_loaded() -> bool:
    try:
        body = TestClient(app).get("/health").json()
        return bool(body.get("ok")) and int(body.get("plants") or 0) >= 6
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(_pack_loaded(), "twin disruption pack / logistics_scm not loaded")
class TwinApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_index_html(self):
        """Feature: Twin UI
        Expectation: GET / returns the world snapshot HTML.
        """
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("World snapshot", r.text)

    def test_health(self):
        """Feature: Twin health
        Expectation: GET /health is ok with at least 6 plants.
        """
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(body["plants"], 6)

    def test_days_of_cover(self):
        """Feature: Twin days of cover
        Expectation: MAT-SEMI-1 includes Chennai and Bangalore with two short-cover plants.
        """
        r = self.client.get("/v1/twin/inventory/days-of-cover", params={"material": "MAT-SEMI-1"})
        self.assertEqual(r.status_code, 200)
        ids = {p["plant_id"] for p in r.json()["positions"]}
        self.assertTrue({"PL-IN-CHN", "PL-IN-BLR"} <= ids)

    def test_open_pos_abc(self):
        """Feature: Twin open POs
        Expectation: VEND-ABC open POs include PO-1842.
        """
        r = self.client.get("/v1/twin/pos/open", params={"vendor": "VEND-ABC"})
        self.assertEqual(r.status_code, 200)
        ids = {p["po_id"] for p in r.json()["purchase_orders"]}
        self.assertIn("PO-1842", ids)

    def test_news(self):
        """Feature: Twin news
        Expectation: News signals include NEWS-ABC-SHUTDOWN.
        """
        r = self.client.get("/v1/twin/signals/news")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(s["signal_id"] == "NEWS-ABC-SHUTDOWN" for s in r.json()["signals"]))

    def test_plants_at_risk(self):
        """Feature: Twin plants at risk
        Expectation: Chennai has 5 days of cover and ABC open qty.
        """
        r = self.client.get("/v1/twin/plants-at-risk")
        self.assertEqual(r.status_code, 200)
        plants = r.json()["plants"]
        chennai = next(p for p in plants if p["plant_id"] == "PL-IN-CHN")
        self.assertEqual(chennai["days_of_cover"], 5)
        self.assertGreater(chennai["open_qty_from_abc"], 0)

    def test_mcp_inventory_call(self):
        """Feature: Twin MCP inventory tool
        Expectation: inventory.days_of_cover returns Chennai and Bangalore.
        """
        r = self.client.post(
            "/v1/mcp/tools/call",
            json={"name": "inventory.days_of_cover", "arguments": {"material_id": "MAT-SEMI-1"}},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body.get("error"))
        ids = {p["plant_id"] for p in body["result"]}
        self.assertTrue({"PL-IN-CHN", "PL-IN-BLR"} <= ids)

    def test_mcp_po_and_news_tools(self):
        """Feature: Twin MCP PO and news tools
        Expectation: po.open_for_vendor and news.search succeed on the disruption pack.
        """
        pos = self.client.post(
            "/v1/mcp/tools/call",
            json={"name": "po.open_for_vendor", "arguments": {"vendor_id": "VEND-ABC"}},
        )
        self.assertEqual(pos.status_code, 200)
        news = self.client.post(
            "/v1/mcp/tools/call",
            json={"name": "news.search", "arguments": {"entity_id": "VEND-ABC"}},
        )
        self.assertEqual(news.status_code, 200)
        self.assertFalse(news.json().get("error"))
