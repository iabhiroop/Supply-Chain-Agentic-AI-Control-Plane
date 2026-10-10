"""Feature/Expectation: Project and named-agent $ buckets."""


def test_budget_endpoint_has_named_rows(client):
    """Feature: Budget status
    Expectation: GET /v1/budget includes a project limit and named_agents map.
    """
    res = client.get("/v1/budget")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scope"] == "project"
    assert body["limit_usd"] > 0
    named = body["named_agents"]
    assert "Procurement_Agent" in named
    assert "Disruption_Orchestrator" in named
    assert "Budget_Probe" in named
    assert named["Procurement_Agent"]["scope_id"] != named["Disruption_Orchestrator"]["scope_id"]


def test_budget_probe_returns_429(client, gateway, do_chat):
    """Feature: Budget_Probe $0 bucket
    Expectation: X-Agent-Id: Budget_Probe returns 429 and does not call the stub.
    """
    res = do_chat(client, "probe budget", headers={"X-Agent-Id": "Budget_Probe"})
    assert res.status_code == 429, res.text
    detail = res.json()["detail"]
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "AGENT_BUDGET_EXCEEDED"
    assert gateway["gemini"].calls == 0


def test_named_agent_filter(client):
    """Feature: Named agent budget filter
    Expectation: GET /v1/budget?agent_id=Procurement_Agent returns that agent's scope.
    """
    res = client.get("/v1/budget", params={"agent_id": "Procurement_Agent"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scope"] == "agent"
    assert body["scope_id"] == "agent:Procurement_Agent"
    assert "project" in body
