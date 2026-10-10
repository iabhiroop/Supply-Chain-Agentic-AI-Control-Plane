"""Feature/Expectation: Exact and opt-in semantic cache."""

TAIWAN = (
    "A major supplier in Taiwan has announced a production shutdown. "
    "Which open purchase orders are at risk, and what should procurement do?"
)
TAIWAN_PARA = (
    "Taiwan supplier shutdown — which open POs are exposed, and what should procurement do?"
)


def test_repeat_chat_is_exact_hit(client, gateway, do_chat):
    """Feature: Exact-match cache
    Expectation: The second identical chat is a cache hit and does not call the stub again.
    """
    first = do_chat(client, "cache-me-exact")
    assert first.status_code == 200, first.text
    calls = gateway["gemini"].calls
    second = do_chat(client, "cache-me-exact")
    assert second.status_code == 200, second.text
    assert second.headers.get("x-cache-hit") == "true"
    assert gateway["gemini"].calls == calls


def test_no_cache_header_misses(client, gateway, do_chat):
    """Feature: Cache bypass
    Expectation: X-Cache-Control: no-cache forces a second provider call for the same prompt.
    """
    headers = {"X-Cache-Control": "no-cache"}
    first = do_chat(client, "bypass-cache", headers=headers)
    assert first.status_code == 200, first.text
    calls = gateway["gemini"].calls
    second = do_chat(client, "bypass-cache", headers=headers)
    assert second.status_code == 200, second.text
    assert second.headers.get("x-cache-hit") == "false"
    assert gateway["gemini"].calls == calls + 1


def test_semantic_opt_in_paraphrase_hits(client, gateway, do_chat):
    """Feature: Opt-in semantic cache
    Expectation: With X-Semantic-Cache: on, a Taiwan paraphrase is a semantic_hit.
    """
    headers = {"X-Semantic-Cache": "on"}
    first = do_chat(client, TAIWAN, headers=headers)
    assert first.status_code == 200, first.text
    calls = gateway["gemini"].calls
    second = do_chat(client, TAIWAN_PARA, headers=headers)
    assert second.status_code == 200, second.text
    assert second.headers.get("x-cache-hit") == "true"
    assert second.headers.get("x-cache-status") == "semantic_hit"
    assert gateway["gemini"].calls == calls


def test_default_traffic_does_not_semantic_hit(client, gateway, do_chat):
    """Feature: Semantic cache off by default
    Expectation: Without X-Semantic-Cache, a paraphrase is a miss and calls the provider again.
    """
    first = do_chat(client, TAIWAN)
    assert first.status_code == 200, first.text
    calls = gateway["gemini"].calls
    second = do_chat(client, TAIWAN_PARA)
    assert second.status_code == 200, second.text
    assert second.headers.get("x-cache-hit") == "false"
    assert gateway["gemini"].calls == calls + 1
