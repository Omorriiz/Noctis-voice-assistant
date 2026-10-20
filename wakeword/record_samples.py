"""Запись СВОИХ примеров слова пробуждения с микрофона ПК (повышает качество на вашем голосе).

  pip install sounddevice
  python record_samples.py --workdir D:/wake --count 30

Для каждого примера: Enter → скажите слово → запись 1,5 с. Файлы идут в <workdir>/generated_samples/user_XXXX.wav
(16 кГц, моно) и участвуют в следующем build_features.py. Говорите по-разному: громко/тихо, близко/далеко, быстро/медленно.
"""
import argparse
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile

from make_samples import trim


def main():
    import sounddevice as sd
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--seconds", type=float, default=1.5)
    args = ap.parse_args()
    out = args.workdir / "generated_samples"
    out.mkdir(parents=True, exist_ok=True)
    start = len(list(out.glob("user_*.wav")))
    for i in range(args.count):
        input(f"[{i + 1}/{args.count}] Enter — и скажите слово пробуждения… ")
        audio = sd.rec(int(args.seconds * 16000), samplerate=16000, channels=1, dtype="int16")
        sd.wait()
        clip = trim(audio[:, 0].astype(np.float32) / 32768.0)
        if len(clip) < 1600:
            print("  слишком тихо — пропуск")
            continue
        wavfile.write(out / f"user_{start + i:04d}.wav", 16000, (np.clip(clip, -1, 1) * 32767).astype(np.int16))
    print("Готово. Теперь: build_features.py → train.py")


if __name__ == "__main__":
    main()
