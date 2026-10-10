"""Feature/Expectation: Admin CRUD and UI."""


def test_admin_keys_list_after_create(client, issue_key):
    """Feature: Admin virtual keys
    Expectation: POST /v1/admin/keys then GET /v1/admin/keys includes the new name.
    """
    issue_key(client, name="admin-list-me")
    res = client.get("/v1/admin/keys")
    assert res.status_code == 200, res.text
    names = {row["name"] for row in res.json()}
    assert "admin-list-me" in names


def test_admin_dashboard_cache_routes_models(client):
    """Feature: Admin dashboard surfaces
    Expectation: dashboard, cache, routes, and models return 200 in sqlite/dev.
    """
    for path in (
        "/v1/admin/dashboard",
        "/v1/admin/cache",
        "/v1/admin/routes",
        "/v1/admin/models",
        "/v1/models",
    ):
        res = client.get(path)
        assert res.status_code == 200, f"{path}: {res.text}"


def test_admin_ui_html(client):
    """Feature: Admin UI
    Expectation: GET /admin/ serves HTML.
    """
    res = client.get("/admin/")
    assert res.status_code == 200, res.text
    ctype = res.headers.get("content-type", "").lower()
    assert "html" in ctype or "<" in res.text.lower()
