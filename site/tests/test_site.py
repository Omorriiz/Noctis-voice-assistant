r"""Проверки статического сайта: тексты, ссылки, внешние запросы, безопасность скрипта, контраст цветов.

    ..\server\.venv\Scripts\python.exe -m pytest site\tests      (из корня проекта)
"""
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SITE))
import build  # noqa: E402
from i18n import DASH_KEYS, LANGS, TEXT  # noqa: E402

PAGES = [("ru", SITE / "index.html"), ("ru", SITE / "dashboard.html")] + \
    [(l, SITE / l / p) for l in LANGS if l != "ru" for p in ("index.html", "dashboard.html")]


def shape(v):
    if isinstance(v, dict):
        return {k: shape(x) for k, x in v.items()}
    if isinstance(v, list):
        return [shape(x) for x in v]
    return type(v).__name__


def test_five_languages_ru_first():
    assert LANGS == ["ru", "en", "es", "de", "fr"]


@pytest.mark.parametrize("lang", LANGS)
def test_same_structure_as_russian(lang):
    ru = TEXT["ru"]
    t = TEXT[lang]
    assert set(t) == set(ru)
    for k in ru:
        if k in ("stats", "features", "how_steps", "flow", "say", "privacy", "hw"):
            assert len(t[k]) == len(ru[k]), k
        elif isinstance(ru[k], dict):
            assert set(t[k]) == set(ru[k]), k
    assert len(t["dash"]["days"]) == 7
    assert set(t["dash"]["melodies"]) == {"default", "soft", "rooster", "beeps"}


@pytest.mark.parametrize("lang", LANGS)
def test_no_empty_strings_and_no_copied_russian(lang):
    def walk(v, path):
        if isinstance(v, dict):
            for k, x in v.items():
                yield from walk(x, path + [k])
        elif isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                yield from walk(x, path + [i])
        else:
            yield path, v
    for path, v in walk(TEXT[lang], []):
        assert str(v).strip(), path
        if lang != "ru" and isinstance(v, str) and path[0] not in ("html_lang",):
            assert not re.search("[а-яА-Я]", v), (lang, path)


def test_dash_keys_complete_and_used_keys_exist():
    js = (SITE / "assets" / "dashboard.js").read_text(encoding="utf-8")
    used = (set(re.findall(r'\bt\("([a-z_0-9]+)"', js)) - {"tab_"}) | {"tab_speaker", "tab_timers", "tab_music", "tab_history"}
    for lang in LANGS:
        d = TEXT[lang]["dash"]
        for k in DASH_KEYS:
            assert k in d, (lang, k)
        assert used <= set(d), (lang, used - set(d))


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs, self.scripts, self.attrs = [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.attrs.append((tag, a))
        for k in ("href", "src"):
            if k in a:
                self.refs.append((tag, a[k]))
        if tag == "script" and "src" not in a:
            self.scripts.append(tag)


@pytest.mark.parametrize("lang,path", PAGES)
def test_page_links_and_privacy(lang, path):
    text = path.read_text(encoding="utf-8")
    c = Collect()
    c.feed(text)
    assert f'<html lang="{lang}">' in text
    assert "<title>" in text and 'name="viewport"' in text
    assert not c.scripts, "встроенные скрипты запрещены (нужна строгая CSP)"
    assert len(re.findall(r'rel="alternate" hreflang=', text)) == 5
    for tag, ref in c.refs:
        if ref.startswith("mailto:"):
            continue
        assert not re.match(r"^(https?:)?//", ref), f"внешний адрес {ref}"
        target = ref.split("#")[0]
        if target:
            assert (path.parent / target).resolve().exists(), f"{path}: нет {ref}"
    assert 'id="theme-toggle"' in text and 'id="lang-select"' in text


@pytest.mark.parametrize("lang", LANGS)
def test_pages_are_up_to_date_with_build(lang):
    out = SITE if lang == "ru" else SITE / lang
    assert (out / "index.html").read_text(encoding="utf-8") == build.landing(lang), "запустите python build.py"
    assert (out / "dashboard.html").read_text(encoding="utf-8") == build.dashboard(lang), "запустите python build.py"
    js = (SITE / "assets" / f"i18n.{lang}.js").read_text(encoding="utf-8")
    assert json.loads(js[js.index("=") + 1:].strip().rstrip(";"))["dash"] == TEXT[lang]["dash"]


def test_dashboard_script_does_not_inject_html():
    js = (SITE / "assets" / "dashboard.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert bad not in js, bad


def test_no_external_urls_in_assets():
    for f in ("site.css", "site.js", "dashboard.js", "tokens.css"):
        text = (SITE / "assets" / f).read_text(encoding="utf-8")
        assert not re.search(r"https?://(?!localhost|…|www.w3.org)", text), f


def lum(hex_):
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_color_contrast_wcag_aa(theme):
    t = json.loads((SITE / "assets" / "tokens.json").read_text(encoding="utf-8"))[theme]
    for fg, bg in [("text", "bg-0"), ("text", "bg-1"), ("text", "bg-2"), ("text-muted", "bg-0"), ("text-muted", "bg-1"), ("text-muted", "bg-2"),
                   ("text-faint", "bg-0"), ("text-faint", "bg-2"), ("violet-300", "bg-0"), ("violet-300", "bg-1"), ("violet-300", "bg-2"),
                   ("on-brand", "violet-700"), ("on-brand", "violet-600"), ("on-brand", "blue-600"),
                   ("success", "bg-2"), ("danger", "bg-2"), ("danger", "bg-0"), ("warning", "bg-2")]:
        assert contrast(t[fg], t[bg]) >= 4.5, (theme, fg, bg, round(contrast(t[fg], t[bg]), 2))


def test_tokens_css_has_both_themes():
    css = (SITE / "assets" / "tokens.css").read_text(encoding="utf-8")
    assert ':root[data-theme="light"]' in css and "--on-brand" in css and "--violet-700" in css and css.index("--bg-0: #05030A") < css.index("--bg-0: #F6F3FD")


def test_brand_images_exist_and_are_used():
    css = (SITE / "assets" / "site.css").read_text(encoding="utf-8")
    for name in re.findall(r'url\("(img/[^"]+)"\)', css):
        assert (SITE / "assets" / name).stat().st_size > 1000, name
    assert (SITE / "assets" / "img" / "noctis-icon.webp").exists()
    for lang, path in PAGES:
        text = path.read_text(encoding="utf-8")
        assert "noctis-icon.webp" in text and "brand-art" in (text if path.name == "index.html" else "brand-art"), path


@pytest.mark.parametrize("lang,page", PAGES)
def test_apk_download_link(lang, page):
    """Каждая страница ведёт на APK в site/downloads/ (сам файл в git не входит)."""
    html = page.read_text(encoding="utf-8")
    prefix = "" if lang == "ru" else "../"
    assert f'href="{prefix}downloads/{build.APK_NAME}"' in html
    assert build.APK_NAME == f"Noctis-{build.APP_VERSION}.apk"
    assert "app_dl" in TEXT[lang] and TEXT[lang]["app_dl"] in html


@pytest.mark.parametrize("lang,page", PAGES)
def test_ios_notice(lang, page):
    if page.name == "index.html":
        assert TEXT[lang]["app_ios"] in page.read_text(encoding="utf-8")


def test_admin_page_is_hidden_and_safe():
    html = (SITE / "admin.html").read_text(encoding="utf-8")
    assert 'name="robots" content="noindex' in html
    assert "<script>" not in html and "javascript:" not in html           # только внешние файлы
    assert "admin.html" not in (SITE / "index.html").read_text(encoding="utf-8").replace('content="admin.html"', "")  # нет ссылки в меню
    js = (SITE / "assets" / "admin.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert bad not in js, bad
    assert not re.search(r"https?://(?!localhost)", js)                    # никаких внешних адресов
    assert "password_hash" not in js


@pytest.mark.parametrize("lang,page", PAGES)
def test_alt_x_target_is_set(lang, page):
    html = page.read_text(encoding="utf-8")
    prefix = "" if lang == "ru" else "../"
    assert f'<meta name="noctis-admin" content="{prefix}admin.html">' in html
    assert "KeyX" in (SITE / "assets" / "site.js").read_text(encoding="utf-8")


def test_server_field_hidden_by_default_and_seven_taps():
    js = (SITE / "assets" / "dashboard.js").read_text(encoding="utf-8")
    assert "taps >= 7" in js and 'noctis-dev' in js
    assert "/health" in js and "devMode()" in js
    cfg = (SITE / "assets" / "config.js").read_text(encoding="utf-8")
    assert "showServerField: false" in cfg


@pytest.mark.parametrize("lang", LANGS)
def test_delete_device_texts_in_every_language(lang):
    dash = TEXT[lang]["dash"]
    for key in ("dev_delete", "dev_delete_confirm", "dev_delete_online", "dev_delete_offline", "dev_deleted"):
        assert dash.get(key), (lang, key)
    assert "{name}" in dash["dev_delete_confirm"]


def test_dashboard_has_delete_device_button():
    js = (SITE / "assets" / "dashboard.js").read_text(encoding="utf-8")
    assert 't("dev_delete")' in js and 'api("DELETE", "/api/v1/devices/"' in js
    assert 'window.confirm(t("dev_delete_confirm"' in js   # без подтверждения не удаляется


@pytest.mark.parametrize("lang", LANGS)
def test_home_city_texts_and_weather_credit(lang):
    dash = TEXT[lang]["dash"]
    assert dash.get("home_city") and dash.get("home_city_hint")
    assert "Open-Meteo" in TEXT[lang]["credits"] and "CC BY 4.0" in TEXT[lang]["credits"]      # условие лицензии данных о погоде
    page = (SITE if lang == "ru" else SITE / lang) / "index.html"
    assert "Open-Meteo" in page.read_text(encoding="utf-8")


def test_dashboard_sends_home_city():
    js = (SITE / "assets" / "dashboard.js").read_text(encoding="utf-8")
    assert "home_city: homeCity.value.trim()" in js and 'value: s.home_city || ""' in js


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_tab_texts_in_every_language(lang):
    dash = TEXT[lang]["dash"]
    for key in ("tab_privacy", "priv_intro", "priv_export", "priv_exported", "priv_clear", "priv_clear_confirm", "priv_cleared",
                "priv_delete", "priv_delete_confirm", "priv_delete_pw", "priv_deleted"):
        assert dash.get(key), (lang, key)
    assert "{n}" in dash["priv_cleared"]


def test_privacy_panel_works_only_on_request_and_asks_confirmation():
    js = (SITE / "assets" / "dashboard.js").read_text(encoding="utf-8")
    assert '"/api/v1/me/export"' in js and 'api("DELETE", "/api/v1/me/history")' in js and 'api("DELETE", "/api/v1/me", { password: pw.value })' in js
    assert 'window.confirm(t("priv_clear_confirm"))' in js and 'window.confirm(t("priv_delete_confirm"))' in js
    assert "setInterval" not in js[js.index("function privacyPanel"):js.index("function tabPrivacy")]        # никаких автоматических действий
