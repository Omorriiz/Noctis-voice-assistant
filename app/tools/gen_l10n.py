"""Создаёт lib/l10n/strings.g.dart: строки приложения на 5 языках.

    python tools/gen_l10n.py

Источники: кабинет сайта (site/i18n.py, ключи DASH) + строки только приложения (tools/app_strings.py) + несколько заголовков лендинга.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "site"))
sys.path.insert(0, str(Path(__file__).parent))

from i18n import LANGS, LANG_NAMES, TEXT  # noqa: E402
from app_strings import APP  # noqa: E402

OUT = ROOT / "lib" / "l10n" / "strings.g.dart"
TOP_KEYS = ("signin", "h1a", "h1b", "lead", "privacy_h")


def q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("$", "\\$").replace("\n", "\\n") + "'"


def flat(lang: str) -> dict:
    d = {}
    dash = TEXT[lang]["dash"]
    for k, v in dash.items():
        if k == "days":
            for i, x in enumerate(v):
                d[f"day_{i}"] = x
        elif k == "melodies":
            for m, x in v.items():
                d[f"melody_{m}"] = x
        else:
            d[k] = v
    for k in TOP_KEYS:
        d[f"site_{k}"] = TEXT[lang][k]
    d.update(APP[lang])
    return d


def main() -> int:
    data = {lang: flat(lang) for lang in LANGS}
    keys = set(data["ru"])
    for lang in LANGS:
        missing = keys ^ set(data[lang])
        if missing:
            sys.exit(f"{lang}: расхождение ключей {sorted(missing)}")
    lines = ["// Создано tools/gen_l10n.py (из site/i18n.py и tools/app_strings.py) — не правьте вручную.", "",
             "const List<String> kLanguages = [" + ", ".join(q(l) for l in LANGS) + "];", "",
             "const Map<String, String> kLanguageNames = {" + ", ".join(f"{q(l)}: {q(LANG_NAMES[l])}" for l in LANGS) + "};", "",
             "const Map<String, Map<String, String>> kStrings = {"]
    for lang in LANGS:
        lines.append(f"  {q(lang)}: {{")
        for k in sorted(data[lang]):
            lines.append(f"    {q(k)}: {q(data[lang][k])},")
        lines.append("  },")
    lines += ["};", ""]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("создан", OUT, f"({len(keys)} ключей × {len(LANGS)} языков)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
