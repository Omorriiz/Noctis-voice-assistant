"""Упаковка обученной модели в контейнер NWWM (раздел wakeword на плате) и, при желании, загрузка на сервер.

  python pack_model.py --tflite stream_state_internal_quant.tflite --wake-word nils --label "Нильс" \
      --cutoff 0.97 --window 5 --out nils.nwwm
  python pack_model.py ... --upload http://192.168.1.5:8000 --token <admin access token>

Свою фразу: --wake-word "custom:Привет Ноктис" (id для API и настроек устройства).
"""
import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from noctis_server import nwwm  # noqa: E402  (тот же формат, что проверяет сервер и прошивка)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tflite", type=Path, required=True)
    ap.add_argument("--wake-word", required=True, help="id: nils или custom:<фраза>")
    ap.add_argument("--label", default=None, help="название для интерфейса")
    ap.add_argument("--cutoff", type=float, default=0.97, help="порог вероятности (по метрикам обучения)")
    ap.add_argument("--window", type=int, default=5, help="размер скользящего окна")
    ap.add_argument("--arena", type=int, default=40000, help="размер tensor arena, байт (прошивка при нехватке увеличит)")
    ap.add_argument("--notes", default="")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--upload", metavar="SERVER_URL", help="например http://192.168.1.5:8000")
    ap.add_argument("--token", help="access-токен администратора (с --upload)")
    args = ap.parse_args()

    model = args.tflite.read_bytes()
    manifest = {"version": 1, "wake_word": args.wake_word, "label": args.label or args.wake_word,
                "feature_step_size": 10, "probability_cutoff": args.cutoff, "sliding_window_size": args.window,
                "tensor_arena_size": args.arena, "created": date.today().isoformat(),
                "model_sha256": hashlib.sha256(model).hexdigest(), "notes": args.notes}
    blob = nwwm.pack(manifest, model)
    nwwm.parse(blob)  # самопроверка
    out = args.out or args.tflite.with_suffix(".nwwm")
    out.write_bytes(blob)
    print(f"{out}: {len(blob)} байт, sha256 {hashlib.sha256(blob).hexdigest()}")

    if args.upload:
        url = f"{args.upload.rstrip('/')}/api/v1/wakewords?wake_word={urllib.parse.quote(args.wake_word)}"
        req = urllib.request.Request(url, data=blob, method="PUT",
                                     headers={"Authorization": f"Bearer {args.token}",
                                              "Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req) as r:
            print("Сервер:", r.status, json.loads(r.read()))


if __name__ == "__main__":
    main()
