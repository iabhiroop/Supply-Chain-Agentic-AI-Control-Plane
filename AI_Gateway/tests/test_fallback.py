"""Feature/Expectation: Fallback and provider 429 mapping."""

import json


def test_gemini_fail_falls_back_to_local(client, gateway, do_chat):
    """Feature: Gemini to local fallback
    Expectation: A retryable gemini error is served by the local stub with x-fallback-used true.
    """
    gateway["gemini"].fail_code = "provider_unavailable"
    res = do_chat(client, "need fallback")
    assert res.status_code == 200, res.text
    assert res.headers.get("x-fallback-used") == "true"
    assert res.json()["choices"][0]["message"]["content"] == "local-stub"
    assert gateway["local"].calls >= 1


def test_provider_rate_limit_stays_429(client, gateway, do_chat):
    """Feature: Provider 429 mapping
    Expectation: When every hop raises rate_limited, the gateway returns 429 not 502.
    """
    gateway["gemini"].fail_code = "rate_limited"
    gateway["local"].fail_code = "rate_limited"
    res = do_chat(client, "all hops limited")
    assert res.status_code == 429, res.text
    assert res.status_code != 502
    assert "retry-after" in {h.lower() for h in res.headers.keys()}
    detail = res.json()["detail"]
    if isinstance(detail, dict):
        assert str(detail.get("code", "")).lower() in {"rate_limited", "rate_limit_exceeded"}
    else:
        assert "rate" in json.dumps(detail).lower()
