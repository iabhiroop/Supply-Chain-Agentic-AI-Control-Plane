"""Feature/Expectation: RPM limiter."""


def test_key_rpm_burst_is_429_not_502(client, do_chat, issue_key):
    """Feature: Virtual-key RPM
    Expectation: A second call on an rpm=1 key returns 429 with Retry-After, not 502.
    """
    key = issue_key(client, name="rpm-one", rpm=1, tpm=100_000)
    headers = {
        "Authorization": f"Bearer {key}",
        "X-Cache-Control": "no-cache",
    }
    first = do_chat(client, "rpm-a", headers=headers)
    assert first.status_code == 200, first.text
    second = do_chat(client, "rpm-b", headers=headers)
    assert second.status_code == 429, second.text
    assert second.status_code != 502
    assert "retry-after" in {h.lower() for h in second.headers.keys()}
    detail = second.json()["detail"]
    if isinstance(detail, dict):
        assert detail.get("code") == "RATE_LIMITED"
