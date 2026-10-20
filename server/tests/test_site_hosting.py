"""Сайт отдаётся самим сервером: страницы и файлы — да, исходники и тесты — нет, API имеет приоритет."""


def test_site_index_and_pages_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert client.get("/dashboard.html").status_code == 200
    assert client.get("/admin.html").status_code == 200
    assert client.get("/en/").status_code == 200
    assert client.get("/assets/dashboard.js").status_code == 200


def test_api_still_wins_over_site(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api").json()["status"] == "running"
    assert client.get("/api/v1/admin/stats").status_code in (401, 403)


def test_site_sources_not_exposed(client):
    for path in ("/build.py", "/i18n.py", "/README.md", "/tests/test_site.py", "/start-site.bat", "/../server/.env",
                 "/%2e%2e/server/.env"):
        assert client.get(path).status_code == 404, path


def test_site_defaults_to_same_origin(client):
    assert 'defaultServer: ""' in client.get("/assets/config.js").text
