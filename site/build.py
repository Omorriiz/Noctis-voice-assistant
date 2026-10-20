"""Собирает статический сайт Noctis: лендинг и кабинет на 5 языках.

    python build.py

Результат (готовые файлы лежат в репозитории, для публикации сборка не нужна):
    index.html, dashboard.html            — русский (основной)
    en/ es/ de/ fr/ → index.html, dashboard.html
    assets/tokens.css                     — из assets/tokens.json (общие цвета и шрифты с приложением)
    assets/i18n.<язык>.js                 — строки кабинета
Файл assets/config.js (адрес сервера по умолчанию) сборка не трогает — его правит владелец сайта.
Оформление — assets/site.css (космический тёмный стиль исходного сайта + светлая тема).
"""
import html
import json
from pathlib import Path

from i18n import LANG_NAMES, LANGS, TEXT

ROOT = Path(__file__).parent
ASSETS = ROOT / "assets"
APP_VERSION = next(l.split(":")[1].strip().split("+")[0] for l in (ROOT.parent / "app" / "pubspec.yaml").read_text(encoding="utf-8").splitlines() if l.startswith("version:"))
APK_NAME = f"Noctis-{APP_VERSION}.apk"          # файл кладётся в site/downloads/ (в git не входит)
_apk = ROOT / "downloads" / APK_NAME
APK_MB = round(_apk.stat().st_size / 1048576) if _apk.exists() else 57
CONTACT_EMAIL = "info@tams.ru"   # адрес из исходного сайта; замените на свой

ICONS = {
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0014 0M12 18v3"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "music": '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
    "screen": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "wifi": '<path d="M2 9a15 15 0 0120 0M5.5 12.5a10 10 0 0113 0M9 16a5 5 0 016 0"/><circle cx="12" cy="19.2" r="1"/>',
    "phone": '<rect x="7" y="2" width="10" height="20" rx="2.5"/><path d="M11 18h2"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 13A9 9 0 1111 3a7 7 0 0010 10z"/>',
}
STEP_ICONS = ["wifi", "mic", "screen"]


def icon(name, cls=""):
    return f'<svg viewBox="0 0 24 24" aria-hidden="true"{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{ICONS[name]}</svg>'


def esc(s):
    return html.escape(s, quote=True)


def glow_last(text):
    """Последнее слово заголовка — с неоновым градиентом, как в исходном макете."""
    head, _, last = text.rpartition(" ")
    return f'{esc(head)} <span class="text-glow">{esc(last)}</span>' if head else f'<span class="text-glow">{esc(last)}</span>'


def build_tokens_css():
    tokens = json.loads((ASSETS / "tokens.json").read_text(encoding="utf-8"))

    def block(values):
        return "".join(f"  --{k}: {v};\n" for k, v in values.items())

    shared = (f"  --font-head: {tokens['font']['head']};\n  --font-body: {tokens['font']['body']};\n"
              f"  --radius-sm: {tokens['radius']['sm']};\n  --radius: {tokens['radius']['md']};\n  --radius-lg: {tokens['radius']['lg']};\n")
    css = ("/* Создано build.py из tokens.json — не правьте вручную. */\n"
           f":root{{\n{shared}{block(tokens['dark'])}}}\n"
           f":root[data-theme=\"light\"]{{\n{block(tokens['light'])}}}\n")
    (ASSETS / "tokens.css").write_text(css, encoding="utf-8")


def i18n_js(lang):
    payload = {"lang": lang, "dash": TEXT[lang]["dash"]}
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    (ASSETS / f"i18n.{lang}.js").write_text(f"window.NOCTIS_I18N = {data};\n", encoding="utf-8")


def rel(lang, target_lang, page):
    """Относительная ссылка со страницы языка lang на страницу page языка target_lang."""
    up = "" if lang == "ru" else "../"
    return f"{up}{'' if target_lang == 'ru' else target_lang + '/'}{page}"


def head(lang, page, title, desc):
    c = TEXT[lang]
    up = "" if lang == "ru" else "../"
    alts = "\n".join(f'  <link rel="alternate" hreflang="{l}" href="{rel(lang, l, page)}">' for l in LANGS)
    return f'''<!DOCTYPE html>
<html lang="{c["html_lang"]}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <meta name="description" content="{esc(desc)}">
  <meta name="color-scheme" content="dark light">
  <meta name="theme-color" content="#05030A">
  <meta name="noctis-admin" content="{up}admin.html">
  <link rel="icon" type="image/webp" href="{up}assets/img/noctis-icon.webp">
  <link rel="apple-touch-icon" href="{up}assets/img/noctis-icon.webp">
  <meta property="og:image" content="{up}assets/img/noctis-icon.webp">
{alts}
  <link rel="stylesheet" href="{up}assets/tokens.css">
  <link rel="stylesheet" href="{up}assets/site.css">
  <script src="{up}assets/site.js"></script>
</head>'''


def header(lang, page):
    c = TEXT[lang]
    nav = c["nav"]
    links = [("features", "features"), ("how", "how"), ("voice", "voice"), ("privacy", "privacy"), ("hardware", "hardware")]
    items = "\n".join(f'          <li><a class="nav__link" href="index.html#{a}">{esc(nav[k])}</a></li>' for k, a in links)
    opts = "\n".join(
        f'            <option value="{l}" data-href="{rel(lang, l, page)}"{" selected" if l == lang else ""}>{LANG_NAMES[l]}</option>' for l in LANGS)
    cur = ' aria-current="page"' if page == "dashboard.html" else ""
    up = "" if lang == "ru" else "../"
    return f'''<body>
  <div class="cosmos" aria-hidden="true">
    <span class="stars stars--sm"></span><span class="stars stars--md"></span><span class="stars stars--lg"></span>
    <span class="orb orb--1"></span><span class="orb orb--2"></span><span class="orb orb--3"></span>
  </div>
  <a class="skip" href="#main">{esc(c["skip"])}</a>
  <header class="header">
    <div class="container header__inner">
      <a href="index.html" class="logo" aria-label="Noctis"><img class="logo__img" src="{up}assets/img/noctis-icon.webp" alt="" width="34" height="34">NOCTIS</a>
      <input type="checkbox" id="nav-toggle" class="nav-toggle" aria-label="{esc(c["menu"])}">
      <label for="nav-toggle" class="nav-burger" aria-hidden="true"><span></span></label>
      <nav class="nav" aria-label="{esc(c["menu"])}">
        <ul class="nav__list">
{items}
        </ul>
        <div class="nav__actions">
          <select class="lang-select" id="lang-select" aria-label="{esc(c["lang_label"])}">
{opts}
          </select>
          <button class="icon-btn theme-toggle" id="theme-toggle" type="button" aria-label="{esc(c["theme"])}" title="{esc(c["theme"])}" aria-pressed="true">{icon("sun", "i-sun")}{icon("moon", "i-moon")}</button>
          <a href="dashboard.html" class="btn btn--primary btn--sm nav__cta"{cur}>{esc(c["signin"])}</a>
        </div>
      </nav>
    </div>
  </header>'''


def footer(lang):
    c = TEXT[lang]
    nav = c["nav"]
    up = "" if lang == "ru" else "../"
    return f'''
  <footer class="footer">
    <div class="container">
      <div class="footer__grid footer__grid--4">
        <div class="footer__brand">
          <a href="index.html" class="logo" aria-label="Noctis"><img class="logo__img" src="{up}assets/img/noctis-icon.webp" alt="" width="34" height="34">NOCTIS</a>
          <p class="footer__text">{esc(c["h1a"])} {esc(c["h1b"])}.</p>
        </div>
        <nav class="footer__col" aria-label="{esc(c["footer_nav"])}">
          <h3 class="footer__title">{esc(c["footer_nav"])}</h3>
          <ul>
            <li><a href="index.html#features">{esc(nav["features"])}</a></li>
            <li><a href="index.html#how">{esc(nav["how"])}</a></li>
            <li><a href="index.html#voice">{esc(nav["voice"])}</a></li>
            <li><a href="index.html#privacy">{esc(nav["privacy"])}</a></li>
            <li><a href="dashboard.html">{esc(nav["dashboard"])}</a></li>
          </ul>
        </nav>
        <address class="footer__col">
          <h3 class="footer__title">{esc(c["contact"])}</h3>
          <ul>
            <li><a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></li>
            <li><a href="{("" if lang == "ru" else "../") + "downloads/" + APK_NAME}" download>{esc(c["app_dl"])}</a></li>
          </ul>
        </address>
        <div class="footer__col footer__news">
          <h3 class="footer__title">{esc(c["cta_h"])}</h3>
          <p class="footer__text">{esc(c["cta_p"])}</p>
          <a href="dashboard.html" class="btn btn--ghost btn--sm">{esc(c["cta_btn"])}</a>
        </div>
      </div>
      <div class="footer__bottom">
        <span>© 2026 NOCTIS. {esc(c["rights"])} {esc(c["credits"])}</span>
        <span>{" · ".join(f'<a href="{rel(lang, l, "index.html")}" hreflang="{l}">{l.upper()}</a>' for l in LANGS)}</span>
      </div>
    </div>
  </footer>
</body>
</html>
'''


def section_head(label, title, text=""):
    p = f'\n          <p class="section__text">{esc(text)}</p>' if text else ""
    return f'''<header class="section__head">
          <p class="label">{esc(label)}</p>
          <h2 class="section__title">{glow_last(title)}</h2>{p}
        </header>'''


def landing(lang):
    c = TEXT[lang]
    dl_path = ("" if lang == "ru" else "../") + "downloads/" + APK_NAME
    nav = c["nav"]
    stats = "\n".join(
        f'          <li class="stats__item"><span class="stats__num">{esc(n)}</span><span class="stats__label">{esc(l)}</span></li>' for n, l in c["stats"])
    checks = "\n".join(f"            <li><b>{esc(t)}</b> — <span>{esc(p)}</span></li>" for t, p in c["privacy"])
    features = "\n".join(f'''          <article class="card glass">
            <span class="card__num">0{k}</span>
            <div class="card__icon">{icon(ic)}</div>
            <h3 class="card__title">{esc(t)}</h3>
            <p class="card__text">{esc(p)}</p>
          </article>''' for k, (ic, t, p) in enumerate(c["features"], 1))
    steps = "\n".join(f'''          <article class="metric glass metric--plain">
            <div class="metric__top"><span class="metric__num">0{k}</span><div class="card__icon card__icon--sm">{icon(STEP_ICONS[k - 1])}</div></div>
            <h3 class="metric__title">{esc(t)}</h3>
            <p class="metric__text">{esc(p)}</p>
          </article>''' for k, (t, p) in enumerate(c["how_steps"], 1))
    flow = '<em aria-hidden="true">→</em>'.join(f"<span>{esc(x)}</span>" for x in c["flow"])
    say = "\n".join(f'          <blockquote class="glass">{esc(q)}<small>{esc(h)}</small></blockquote>' for q, h in c["say"])
    hw = "\n".join(f'          <li class="glass"><b>{esc(n)}</b><span>{esc(d)}</span></li>' for n, d in c["hw"])
    dash_ic, dash_title, dash_text = c["features"][5][0], c["features"][5][1], c["features"][5][2]
    return head(lang, "index.html", c["title"], c["desc"]) + "\n" + header(lang, "index.html") + f'''

  <main id="main">
    <section class="hero" id="home">
      <div class="container hero__grid">
        <div class="hero__content">
          <p class="label"><span class="label__dot"></span>{esc(c["eyebrow"])}</p>
          <h1 class="hero__title">{esc(c["h1a"])} <span class="text-glow">{esc(c["h1b"])}</span></h1>
          <p class="hero__sub">{esc(c["lead"])}</p>
          <div class="hero__actions">
            <a href="dashboard.html" class="btn btn--primary">{esc(c["btn_dash"])} <span class="btn__arrow" aria-hidden="true">→</span></a>
            <a href="#features" class="btn btn--ghost">{esc(c["btn_more"])}</a>
          </div>
        </div>
        <div class="hero__visual" aria-hidden="true">
          <div class="brand-art brand-art--head"></div>
          <div class="glass chip chip--top"><span class="chip__dot"></span> {esc(c["chip_listen"])}</div>
          <div class="glass chip chip--bottom"><strong>ESP32-S3</strong> Wi‑Fi · Bluetooth LE</div>
        </div>
      </div>
      <div class="container">
        <ul class="stats glass">
{stats}
        </ul>
      </div>
    </section>

    <section class="section about" id="privacy">
      <div class="container about__grid">
        <div class="about__visual" aria-hidden="true">
          <div class="about__frame glass">
            <span class="about__grid-lines"></span>
            <span class="about__planet"></span>
            <span class="about__streak about__streak--1"></span>
            <span class="about__streak about__streak--2"></span>
            <span class="about__streak about__streak--3"></span>
          </div>
          <div class="about__badge glass">
            <span class="about__badge-num">S3</span>
            <span class="about__badge-text">{esc(c["badge"])}</span>
          </div>
        </div>
        <div class="about__content">
          <p class="label">{esc(nav["privacy"])}</p>
          <h2 class="section__title">{glow_last(c["privacy_h"])}</h2>
          <p class="section__text">{esc(c["desc"])}</p>
          <ul class="checklist">
{checks}
          </ul>
          <a href="#features" class="btn btn--ghost">{esc(nav["features"])} →</a>
        </div>
      </div>
    </section>

    <section class="section services" id="features">
      <div class="container">
        {section_head(nav["features"], c["features_h"], c["features_sub"])}
        <div class="services__grid">
{features}
        </div>
      </div>
    </section>

    <section class="section results" id="how">
      <div class="container">
        {section_head(nav["how"], c["how_h"])}
        <div class="results__grid results__grid--3">
{steps}
        </div>
        <p class="flow">{flow}</p>
      </div>
    </section>

    <section class="section" id="voice">
      <div class="container">
        {section_head(nav["voice"], c["voice_h"], c["voice_sub"])}
        <div class="say">
{say}
        </div>
      </div>
    </section>

    <section class="section" id="hardware">
      <div class="container">
        {section_head(nav["hardware"], c["hw_h"], c["hw_sub"])}
        <ul class="hw">
{hw}
        </ul>
      </div>
    </section>

    <section class="section works" id="control">
      <div class="container">
        {section_head(nav["dashboard"], c["works_h"])}
        <div class="works__list">
          <article class="work glass">
            <div class="work__media work__media--1" aria-hidden="true">
              <span class="work__tag">{esc(nav["dashboard"])}</span>
              <span class="work__icon">{icon(dash_ic)}</span>
            </div>
            <div class="work__body">
              <p class="label label--muted">NOCTIS</p>
              <h3 class="work__title">{esc(dash_title)}</h3>
              <p class="work__text">{esc(dash_text)}</p>
              <a href="dashboard.html" class="btn btn--primary btn--sm work__btn">{esc(c["btn_dash"])} <span class="btn__arrow" aria-hidden="true">→</span></a>
            </div>
          </article>
          <article class="work glass" id="app">
            <div class="work__media work__media--2" aria-hidden="true">
              <span class="work__tag">{esc(c["app_tag"])}</span>
              <span class="work__icon">{icon("phone")}</span>
            </div>
            <div class="work__body">
              <p class="label label--muted">NOCTIS</p>
              <h3 class="work__title">{esc(c["app_h"])}</h3>
              <p class="work__text">{esc(c["app_p"])}</p>
              <a href="{dl_path}" class="btn btn--primary btn--sm work__btn" download>{esc(c["app_dl"])} <span class="btn__arrow" aria-hidden="true">↓</span></a>
              <p class="work__meta">{esc(c["app_meta"].format(v=APP_VERSION, mb=APK_MB))}</p>
              <p class="work__meta">{esc(c["app_how"])}</p>
              <p class="work__ios"><span class="badge-soon">iPhone</span> {esc(c["app_ios"])}</p>
            </div>
          </article>
        </div>
      </div>
    </section>

    <section class="section cta" id="contact">
      <div class="container">
        <div class="cta__box">
          <span class="cta__glow" aria-hidden="true"></span>
          <p class="label">{esc(nav["dashboard"])}</p>
          <h2 class="cta__title">{glow_last(c["cta_h"])}</h2>
          <p class="cta__text">{esc(c["cta_p"])}</p>
          <a href="dashboard.html" class="btn btn--primary btn--lg">{esc(c["cta_btn"])} <span class="btn__arrow" aria-hidden="true">→</span></a>
        </div>
      </div>
    </section>
  </main>
''' + footer(lang)


def dashboard(lang):
    c = TEXT[lang]
    d = c["dash"]
    up = "" if lang == "ru" else "../"
    return head(lang, "dashboard.html", c["dash_title"], c["dash_desc"]) + "\n" + header(lang, "dashboard.html") + f'''

  <main id="main">
    <div class="container dash">
      <p class="label"><span class="label__dot"></span>NOCTIS</p>
      <h1 class="dash__title">{glow_last(d["dash_h"])}</h1>
      <p class="muted">{esc(d["dash_intro"])}</p>
      <div id="dash"><noscript><div class="msg err">{esc(d["noscript"])}</div></noscript></div>
    </div>
  </main>
  <script src="{up}assets/config.js"></script>
  <script src="{up}assets/i18n.{lang}.js"></script>
  <script src="{up}assets/dashboard.js" defer></script>
''' + footer(lang)


def admin_page():
    """Страница администратора: только на русском, не в меню, не индексируется; открывается сочетанием Alt+X."""
    return '''<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Noctis — администратор</title>
  <meta name="robots" content="noindex, nofollow">
  <meta name="color-scheme" content="dark light">
  <meta name="theme-color" content="#05030A">
  <link rel="icon" type="image/webp" href="assets/img/noctis-icon.webp">
  <link rel="stylesheet" href="assets/tokens.css">
  <link rel="stylesheet" href="assets/site.css">
  <script src="assets/site.js"></script>
</head>
<body>
  <div class="cosmos" aria-hidden="true">
    <span class="stars stars--sm"></span><span class="stars stars--md"></span><span class="stars stars--lg"></span>
    <span class="orb orb--1"></span><span class="orb orb--2"></span><span class="orb orb--3"></span>
  </div>
  <header class="header">
    <div class="container header__inner">
      <a href="index.html" class="logo" aria-label="Noctis"><img class="logo__img" src="assets/img/noctis-icon.webp" alt="" width="34" height="34">NOCTIS</a>
      <div class="nav__actions">
        <button class="icon-btn theme-toggle" id="theme-toggle" type="button" aria-label="Тема" title="Тема" aria-pressed="true">''' + icon("sun", "i-sun") + icon("moon", "i-moon") + '''</button>
      </div>
    </div>
  </header>
  <main id="main">
    <div class="container dash">
      <p class="label"><span class="label__dot"></span>NOCTIS</p>
      <h1 class="dash__title">Панель <span class="glow">администратора</span></h1>
      <div id="admin"><noscript><div class="msg err">Для работы страницы нужен JavaScript.</div></noscript></div>
    </div>
  </main>
  <script src="assets/config.js"></script>
  <script src="assets/admin.js" defer></script>
</body>
</html>
'''


def main():
    build_tokens_css()
    for lang in LANGS:
        out = ROOT if lang == "ru" else ROOT / lang
        out.mkdir(exist_ok=True)
        (out / "index.html").write_text(landing(lang), encoding="utf-8")
        (out / "dashboard.html").write_text(dashboard(lang), encoding="utf-8")
        i18n_js(lang)
    (ROOT / "admin.html").write_text(admin_page(), encoding="utf-8")
    print("готово:", ", ".join(LANGS))


if __name__ == "__main__":
    main()
