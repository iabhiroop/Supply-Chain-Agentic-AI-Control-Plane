"""Feature/Expectation: Gateway health surfaces."""


def test_health_liveness(client):
    """Feature: Gateway liveness
    Expectation: GET /health returns 200 with status healthy and no auth.
    """
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "healthy"
    assert "uptime_seconds" in body


def test_ready_reports_circuit_state(client):
    """Feature: Gateway readiness
    Expectation: GET /ready returns ready/degraded and includes a breakers map.
    """
    res = client.get("/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in {"ready", "degraded"}
    assert "breakers" in body
    assert isinstance(body["breakers"], dict)
    assert body.get("dev_mode") is True


def test_providers_status_lists_stubs(client, gateway):
    """Feature: Public provider status
    Expectation: GET /v1/providers/status lists the injected gemini and local adapters.
    """
    res = client.get("/v1/providers/status")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    names = set(body["providers"].keys())
    assert {"gemini", "local"} <= names
    assert "breaker" in body["providers"]["gemini"]
