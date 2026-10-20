"""Оценка обученной модели на НЕЗАВИСИМЫХ данных (голос Windows SAPI «Irina», не Silero, которым делались примеры).

  python evaluate.py --workdir D:/wake --tflite <stream_state_internal_quant.tflite> [--window 5]

Требует папку <workdir>/eval_sapi (make_sapi_eval.ps1): positive/ — слово, decoys/ — похожие слова, speech/ — длинная речь.
Считает по порогам: полноту (доля обнаруженных слов), срабатывания на «обманках» и ложные срабатывания в ЧАС
обычной речи (рефрактерный период 3 с — как перезапуск ожидания на плате). Детектор — как в прошивке:
срабатывание, если среднее вероятностей в окне больше порога.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile

CUTOFFS = [0.5, 0.7, 0.8, 0.85, 0.9, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99]
REFRACTORY = 100  # запусков модели ≈ 3 с (один запуск = 30 мс)


def probs(model, pcm: np.ndarray):
    return np.array(model.predict_clip(pcm, step_ms=10), dtype=np.float32)


def window_means(p: np.ndarray, window: int) -> np.ndarray:
    if len(p) < window:
        return np.zeros(0)
    return np.convolve(p, np.ones(window) / window, mode="valid")


def detections(p: np.ndarray, cutoff: float, window: int) -> int:
    """Число срабатываний с рефрактерным периодом."""
    m, n, i = window_means(p, window), 0, 0
    while i < len(m):
        if m[i] > cutoff:
            n += 1
            i += REFRACTORY
        else:
            i += 1
    return n


def load(path: Path) -> np.ndarray:
    sr, a = wavfile.read(path)
    assert sr == 16000, f"{path}: нужен 16 кГц"
    return a.astype(np.int16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--tflite", type=Path, required=True)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--json", type=Path, default=None, help="сохранить результаты")
    args = ap.parse_args()

    from microwakeword.inference import Model
    model = Model(str(args.tflite))
    ev = args.workdir / "eval_sapi"
    silence = np.zeros(16000, dtype=np.int16)   # секунда тишины до и после: детектору нужен «разгон»

    pos = [probs(model, np.concatenate([silence, load(f), silence])) for f in sorted((ev / "positive").glob("*.wav"))]
    dec = [probs(model, np.concatenate([silence, load(f), silence])) for f in sorted((ev / "decoys").glob("*.wav"))]
    speech_hours, speech = 0.0, []
    for f in sorted((ev / "speech").glob("*.wav")):
        a = load(f)
        speech_hours += len(a) / 16000 / 3600
        speech.append(probs(model, a))

    rows = []
    print(f"положительных: {len(pos)}, обманок: {len(dec)}, речи: {speech_hours * 60:.1f} мин, окно {args.window}")
    print(f"{'порог':>6} {'полнота':>9} {'обманки':>9} {'ложных/час':>11}")
    for c in CUTOFFS:
        recall = np.mean([detections(p, c, args.window) > 0 for p in pos])
        decoy = np.mean([detections(p, c, args.window) > 0 for p in dec])
        fa = sum(detections(p, c, args.window) for p in speech) / max(speech_hours, 1e-9)
        rows.append({"cutoff": c, "recall": float(recall), "decoy_rate": float(decoy), "false_per_hour": float(fa)})
        print(f"{c:>6.2f} {recall:>9.1%} {decoy:>9.1%} {fa:>11.1f}")
    if args.json:
        args.json.write_text(json.dumps({"window": args.window, "positives": len(pos), "decoys": len(dec),
                                         "speech_minutes": speech_hours * 60, "rows": rows}, indent=2))


if __name__ == "__main__":
    main()
