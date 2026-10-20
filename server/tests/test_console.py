"""Тестовая страница /console: отдаётся, использует существующие маршруты API и отключается флагом."""
import re

from fastapi.testclient import TestClient

from noctis_server.config import settings
from noctis_server.main import create_app


def test_console_page_is_served(client):
    r = client.get("/console")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert "Noctis Console" in r.text


def test_console_calls_only_existing_api_routes(client):
    """Каждый путь /api/v1/... в странице должен существовать в OpenAPI (страница не отстаёт от API)."""
    html = client.get("/console").text
    paths = set(client.get("/openapi.json").json()["paths"])
    # шаблонные части (${id}, ${D()}) приводим к параметрам OpenAPI
    used = set(re.findall(r'"(/api/v1/[a-z/\-]+?)(?:[?"/]|\$)', html))
    used |= {"/api/v1/devices/{public_id}" + m for m in re.findall(r"D\(\) \+ \"(/[a-z/]+)", html)}
    for path in used:
        pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", re.escape(path).replace(r"\{", "{").replace(r"\}", "}")) + "/?$"
        assert any(re.match(pattern, p) or p.startswith(path.rstrip("/")) for p in paths), path


def test_console_can_be_disabled(assistant, monkeypatch):
    monkeypatch.setattr(settings, "test_console", False)
    with TestClient(create_app(assistant)) as c:
        assert c.get("/console").status_code == 404
