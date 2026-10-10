"""Feature/Expectation: Usage metering."""


def test_usage_lists_chat_after_call(client, do_chat):
    """Feature: Usage metering
    Expectation: After a traced chat, GET /v1/usage?trace_id= includes that request.
    """
    tid = "pytest-usage-1"
    res = do_chat(client, "please meter me", headers={"X-Trace-Id": tid})
    assert res.status_code == 200, res.text
    usage = client.get("/v1/usage", params={"trace_id": tid})
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["total_requests"] >= 1
    assert body["requests"]
    row = body["requests"][0]
    assert row["gateway_request_id"]
    assert row["total_tokens"] >= 1
