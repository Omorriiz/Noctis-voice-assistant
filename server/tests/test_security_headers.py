"""Этап 8: заголовки безопасности и совместимость страниц сайта со строгой Content-Security-Policy."""
import re
from pathlib import Path

import pytest

from noctis_server.config import settings
from noctis_server.security_headers import CSP, is_site_page

SITE = Path(settings.site_dir)
PAGES = sorted(p.relative_to(SITE).as_posix() for p in SITE.rglob("*.html") if "tests" not in p.parts)


def directive(csp: str, name: str) -> str:
    return next((d.strip() for d in csp.split(";") if d.strip().startswith(name + " ")), "")


@pytest.mark.parametrize("path", ["/", "/dashboard.html", "/admin.html", "/en/", "/assets/dashboard.js", "/assets/site.css"])
def test_site_responses_carry_csp_and_basic_headers(client, path):
    r = client.get(path)
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert directive(csp, "script-src") == "script-src 'self'" and directive(csp, "style-src") == "style-src 'self'"
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp and "*" not in directive(csp, "script-src")
    assert directive(csp, "frame-ancestors") == "frame-ancestors 'none'" and directive(csp, "object-src") == "object-src 'none'"
    assert directive(csp, "default-src") == "default-src 'self'" and directive(csp, "base-uri") == "base-uri 'self'"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY" and "microphone=()" in r.headers["permissions-policy"]


@pytest.mark.parametrize("method,path", [("get", "/health"), ("get", "/api"), ("post", "/api/v1/auth/login"), ("get", "/api/v1/devices")])
def test_api_responses_have_basic_headers_and_no_store_but_no_csp(client, method, path):
    r = getattr(client, method)(path, **({"json": {"email": "a@b.co", "password": "x"}} if method == "post" else {}))
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in r.headers
    if path.startswith("/api"):
        assert r.headers["cache-control"] == "no-store"


def test_service_pages_are_not_broken_by_csp(client):
    for path in ("/docs", "/console"):
        r = client.get(path)
        assert r.status_code == 200 and "content-security-policy" not in r.headers, path
        assert r.headers["x-content-type-options"] == "nosniff"


def test_which_paths_get_csp():
    for site in ("/", "/index.html", "/en/", "/dashboard.html", "/downloads/Noctis-1.3.0.apk"):
        assert is_site_page(site), site
    for other in ("/api/v1/me", "/health", "/docs", "/redoc", "/console", "/noctis/v1/", "/openapi.json"):
        assert not is_site_page(other), other


def test_websocket_still_works_with_the_middleware(client, auth):
    from .helpers import open_ws, paired
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    ctx.__exit__(None, None, None)


# ---- страницы сайта не нарушают политику: без встроенных скриптов, обработчиков событий и стилей
@pytest.mark.parametrize("page", PAGES)
def test_pages_have_no_inline_script_handlers_or_styles(page):
    html = (SITE / page).read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "встроенный скрипт"
    assert not re.search(r"<[a-z][^>]*\son[a-z]+\s*=", html, re.I), "обработчик события в атрибуте"
    assert "<style" not in html.lower() and not re.search(r"<[a-z][^>]*\sstyle\s*=", html, re.I), "встроенные стили"
    assert not re.search(r"javascript:", html, re.I)


def test_scripts_do_not_create_styles_or_eval():
    for js in (SITE / "assets").glob("*.js"):
        if js.name.startswith("i18n"):
            continue
        text = js.read_text(encoding="utf-8")
        for bad in ('setAttribute("style"', "setAttribute('style'", ".cssText", "eval(", "new Function", "innerHTML", "document.write"):
            assert bad not in text, (js.name, bad)
        assert not re.search(r"\bstyle\s*:", text), (js.name, "style-атрибут из скрипта нарушил бы CSP")


def test_csp_constant_matches_what_is_sent(client):
    assert client.get("/").headers["content-security-policy"] == CSP
