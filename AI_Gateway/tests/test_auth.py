"""Feature/Expectation: Virtual keys."""


def test_sqlite_dev_allows_missing_bearer(client, do_chat):
    """Feature: SQLite / dev auth
    Expectation: Missing Authorization is allowed when the database URL is SQLite.
    """
    res = do_chat(client, "unauthenticated ping")
    assert res.status_code == 200, res.text


def test_invalid_bearer_is_401(client, do_chat):
    """Feature: Invalid virtual key
    Expectation: Bearer vk_not-a-real-key returns 401.
    """
    res = do_chat(client, "nope", headers={"Authorization": "Bearer vk_not-a-real-key"})
    assert res.status_code == 401


def test_minted_virtual_key_can_chat(client, do_chat, issue_key):
    """Feature: Scoped virtual key
    Expectation: A key from POST /v1/admin/keys can call chat completions.
    """
    key = issue_key(client, name="chat-ok")
    res = do_chat(client, "with key", headers={"Authorization": f"Bearer {key}"})
    assert res.status_code == 200, res.text
    assert res.json()["choices"]
