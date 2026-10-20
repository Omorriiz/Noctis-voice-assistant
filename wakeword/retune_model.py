"""Меняет чувствительность готовой модели слова (порог и окно) без переобучения и, при желании, загружает её на сервер.

Колонка «не слышит» слово — порог ниже (0,80–0,90) и/или окно меньше (3–5); слишком много ложных срабатываний — наоборот.
Метрики по порогам на тестовых данных — `models/nils_eval_irina_window*.json` (на живом голосе результат хуже, чем на синтетике).

  python retune_model.py --in models/nils.nwwm --cutoff 0.80 --window 5 --out models/nils_c80_w5.nwwm
  python retune_model.py --in models/nils.nwwm --cutoff 0.80 --window 5 --upload http://192.168.0.136:8000 --token <access-токен администратора>

После загрузки колонка сама скачает новую версию при ближайшем получении настроек (или перезагрузите её).
"""
import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from noctis_server import nwwm  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="src", type=Path, required=True)
    ap.add_argument("--cutoff", type=float, required=True, help="порог вероятности 0.5–0.99")
    ap.add_argument("--window", type=int, required=True, help="окно усреднения, 1–10 оценок (≈ 30 мс каждая)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--upload", metavar="SERVER_URL")
    ap.add_argument("--token")
    args = ap.parse_args()
    if not (0.5 <= args.cutoff <= 0.99) or not (1 <= args.window <= 10):
        sys.exit("cutoff должен быть 0.5–0.99, window — 1–10")

    manifest, model = nwwm.parse(args.src.read_bytes())
    old = (manifest["probability_cutoff"], manifest["sliding_window_size"])
    manifest["probability_cutoff"], manifest["sliding_window_size"] = args.cutoff, args.window
    manifest["notes"] = (manifest.get("notes", "") + f" | порог {old[0]}→{args.cutoff}, окно {old[1]}→{args.window}").strip(" |")
    blob = nwwm.pack(manifest, model)
    nwwm.parse(blob)  # самопроверка
    out = args.out or args.src.with_name(args.src.stem + f"_c{int(args.cutoff * 100)}_w{args.window}.nwwm")
    out.write_bytes(blob)
    print(f"{out}: порог {old[0]}→{args.cutoff}, окно {old[1]}→{args.window}, sha256 {hashlib.sha256(blob).hexdigest()[:16]}…")

    if args.upload:
        if not args.token:
            sys.exit("для --upload нужен --token администратора")
        url = f"{args.upload.rstrip('/')}/api/v1/wakewords?wake_word={urllib.parse.quote(manifest['wake_word'])}"
        req = urllib.request.Request(url, data=blob, method="PUT",
                                     headers={"Authorization": f"Bearer {args.token}", "Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req) as r:
            print("Сервер:", r.status, json.loads(r.read()))


if __name__ == "__main__":
    main()
