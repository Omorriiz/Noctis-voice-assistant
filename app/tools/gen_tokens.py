"""Создаёт lib/theme/tokens.g.dart из site/assets/tokens.json — общего источника цветов сайта и приложения.

    python tools/gen_tokens.py

Тест test/tokens_test.dart проверяет, что созданный файл актуален (значения совпадают с сайтом).
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT.parent / "site" / "assets" / "tokens.json"
OUT = ROOT / "lib" / "theme" / "tokens.g.dart"


def to_color(value: str) -> str:
    value = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", value)
    if m:
        return f"Color(0xFF{m.group(1).upper()})"
    m = re.fullmatch(r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([0-9.]+)\s*\)", value)
    if m:
        r, g, b = (int(x) for x in m.groups()[:3])
        a = round(float(m.group(4)) * 255)
        return f"Color(0x{a:02X}{r:02X}{g:02X}{b:02X})"
    raise ValueError(f"не цвет: {value}")


def camel(name: str) -> str:
    parts = name.split("-")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def main() -> int:
    tokens = json.loads(SRC.read_text(encoding="utf-8"))
    names = [k for k in tokens["dark"] if not k.startswith("glow-")]
    lines = ["// Создано tools/gen_tokens.py из site/assets/tokens.json — не правьте вручную.",
             "import 'dart:ui';", "", "class NoctisPalette {", "  const NoctisPalette({"]
    lines += [f"    required this.{camel(n)}," for n in names]
    lines += ["  });", ""]
    lines += [f"  final Color {camel(n)};" for n in names]
    lines += ["}", ""]
    for theme in ("dark", "light"):
        lines.append(f"const NoctisPalette kPalette{theme.capitalize()} = NoctisPalette(")
        for n in names:
            lines.append(f"  {camel(n)}: {to_color(tokens[theme][n])},")
        lines += [");", ""]
    r = tokens["radius"]
    lines += [f"const double kRadiusSm = {float(r['sm'].rstrip('px'))};",
              f"const double kRadius = {float(r['md'].rstrip('px'))};",
              f"const double kRadiusLg = {float(r['lg'].rstrip('px'))};", ""]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("создан", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
