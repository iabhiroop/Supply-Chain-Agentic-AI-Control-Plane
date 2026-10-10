"""Feature/Expectation: Operating Platform BFF with mocked layers."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from os_platform.main import app  # noqa: E402


def test_health():
    """Feature: OS liveness
    Expectation: GET /health returns 200 with service operating-platform.
    """
    res = TestClient(app).get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "operating-platform"


def test_inbox_waiting(monkeypatch):
    """Feature: OS inbox
    Expectation: GET /v1/inbox returns waiting HITL rows from a mocked Layer 2 overview.
    """
    overview = {
        "transactions": [
            {"id": "tx-wait", "status": "AWAITING_HUMAN", "stage": "AWAITING_HUMAN"},
            {"id": "tx-ok", "status": "SETTLED", "stage": "SETTLED"},
        ],
        "kpis": {"open": 1},
    }

    def fake_request(method, url, **kwargs):
        if url.endswith("/v1/execution/overview"):
            return 200, overview
        return 0, {"error": "unexpected", "url": url}

    monkeypatch.setattr("os_platform.compose.request", fake_request)
    res = TestClient(app).get("/v1/inbox")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("unavailable") is False
    assert len(body["waiting"]) == 1
    assert body["waiting"][0]["id"] == "tx-wait"


def test_inbox_uses_overview_waiting(monkeypatch):
    """Feature: OS inbox model HITL
    Expectation: Layer 2 overview.waiting is passed through with options.
    """
    overview = {
        "waiting": [
            {
                "request_id": "req-1",
                "kind": "model",
                "question": "Which carrier?",
                "options": [{"id": "a", "label": "A"}],
            }
        ],
        "kpis": {"hitl": 1},
        "transactions": [],
    }

    def fake_request(method, url, **kwargs):
        if url.endswith("/v1/execution/overview"):
            return 200, overview
        return 0, {"error": "unexpected", "url": url}

    monkeypatch.setattr("os_platform.compose.request", fake_request)
    res = TestClient(app).get("/v1/inbox")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["waiting"][0]["kind"] == "model"
    assert body["waiting"][0]["options"][0]["id"] == "a"


def test_ask_uses_compose(monkeypatch):
    """Feature: OS Taiwan ask
    Expectation: POST /v1/ask returns the mocked compose.ask payload.
    """
    monkeypatch.setattr(
        "os_platform.compose.ask",
        lambda **kwargs: {"ok": True, "question": kwargs.get("question"), "trace_id": kwargs.get("trace_id") or "t1"},
    )
    res = TestClient(app).post("/v1/ask", json={"question": "Taiwan shutdown?", "include_eval": False})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert "Taiwan" in body["question"]
