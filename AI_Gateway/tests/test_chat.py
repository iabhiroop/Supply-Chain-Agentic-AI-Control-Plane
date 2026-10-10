"""Feature/Expectation: OpenAI-compatible chat."""

def test_chat_returns_choices(client, gateway, do_chat):
    """Feature: OpenAI-compatible chat
    Expectation: POST /v1/chat/completions returns 200 JSON with choices; stub provider is called once.
    """
    res = do_chat(client, "hello gateway")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["choices"]
    assert body["choices"][0]["message"]["content"]
    assert gateway["gemini"].calls == 1


def test_omitted_model_uses_default_route(client, do_chat):
    """Feature: Default model route
    Expectation: Omitting model still 200s and uses default_route (x-model-used header).
    """
    res = do_chat(client, "no model field")
    assert res.status_code == 200, res.text
    assert res.headers.get("x-model-used")
    assert res.json()["model"]


def test_trace_id_round_trips_to_usage(client, do_chat):
    """Feature: Trace attribution
    Expectation: X-Trace-Id on chat is returned by GET /v1/usage?trace_id=.
    """
    tid = "pytest-trace-chat-1"
    res = do_chat(client, "meter this", headers={"X-Trace-Id": tid})
    assert res.status_code == 200, res.text
    usage = client.get("/v1/usage", params={"trace_id": tid})
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["trace_id"] == tid
    assert body["total_requests"] >= 1


def test_agent_id_header_is_echoed(client, do_chat):
    """Feature: Named agent header
    Expectation: X-Agent-Id: Procurement_Agent is echoed on the chat response.
    """
    res = do_chat(client, "agent ping", headers={"X-Agent-Id": "Procurement_Agent"})
    assert res.status_code == 200, res.text
    assert res.headers.get("x-agent-id") == "Procurement_Agent"
