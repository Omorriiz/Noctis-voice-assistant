"""Тестовые звуки для проверки качества воспроизведения на колонке (загрузите в библиотеку на странице /console).

    python tools/make_test_audio.py --out test_audio

Создаёт WAV (44,1 кГц, стерео; сервер сам приводит к 16 кГц моно, как для плеера платы):
  01_tone_1khz_steps.wav   — тон 1 кГц, уровни -30 / -20 / -10 / -3 дБ по 4 с: на каком уровне появляется хрип/искажения
  02_sweep_100_7000hz.wav  — плавный свип 100 Гц…7 кГц за 30 с: резонансы корпуса, дребезг, провалы
  03_melody_c_major.wav    — мелодия из чистых нот (30 с): ровность звука, «плавание» высоты
  04_clicks_silence.wav    — 10 с тишины, затем щелчок и снова тишина: шипение усилителя и щелчки при старте/остановке
  05_speech_band_noise.wav — розовый шум в речевой полосе (20 с): общий тембр и разборчивость фона

Колонка воспроизводит звук 16 кГц моно: выше 8 кГц не будет ничего — это ограничение формата, а не дефект.
"""
import argparse
import math
import random
import struct
import wave
from pathlib import Path

SR = 44100


def write(path: Path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = bytearray()
        for v in samples:
            s = max(-32768, min(32767, int(v)))
            frames += struct.pack("<hh", s, s)
        w.writeframes(bytes(frames))
    print("создан", path)


def db(x):
    return 32767 * 10 ** (x / 20)


def tone_steps():
    out = []
    for level in (-30, -20, -10, -3):
        a = db(level)
        out += [a * math.sin(2 * math.pi * 1000 * i / SR) for i in range(SR * 4)]
    return out


def sweep():
    secs, f0, f1 = 30, 100.0, 7000.0
    k = (f1 / f0) ** (1 / secs)
    out, phase = [], 0.0
    for i in range(SR * secs):
        t = i / SR
        f = f0 * k ** t
        phase += 2 * math.pi * f / SR
        out.append(db(-12) * math.sin(phase))
    return out


def melody():
    notes = [261.63, 293.66, 329.63, 349.23, 392.0, 440.0, 493.88, 523.25, 493.88, 440.0, 392.0, 349.23, 329.63, 293.66, 261.63, 261.63]
    out = []
    for n in range(2):
        for f in notes:
            for i in range(int(SR * 0.9)):
                env = min(1, i / (SR * 0.03)) * min(1, (SR * 0.9 - i) / (SR * 0.08))
                out.append(db(-10) * env * (0.7 * math.sin(2 * math.pi * f * i / SR) + 0.3 * math.sin(2 * math.pi * 2 * f * i / SR)))
    return out[: SR * 30]


def clicks():
    out = [0.0] * (SR * 10)
    out += [db(-6) if i < 60 else 0.0 for i in range(SR * 2)]
    out += [0.0] * (SR * 8)
    return out


def pink_noise(secs=20):
    rnd = random.Random(1)
    b0 = b1 = b2 = 0.0
    out = []
    for _ in range(SR * secs):
        white = rnd.uniform(-1, 1)
        b0 = 0.99765 * b0 + white * 0.0990460
        b1 = 0.96300 * b1 + white * 0.2965164
        b2 = 0.57000 * b2 + white * 1.0526913
        out.append(db(-18) * (b0 + b1 + b2 + white * 0.1848) / 2)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="test_audio")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write(out / "01_tone_1khz_steps.wav", tone_steps())
    write(out / "02_sweep_100_7000hz.wav", sweep())
    write(out / "03_melody_c_major.wav", melody())
    write(out / "04_clicks_silence.wav", clicks())
    write(out / "05_speech_band_noise.wav", pink_noise())


if __name__ == "__main__":
    main()
