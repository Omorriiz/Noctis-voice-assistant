"""Синтез примеров для обучения слова пробуждения голосами Silero (русский, нативно на Windows).

  python make_samples.py --workdir D:/wake --phrase "Нильс" --count 2000          # положительные
  python make_samples.py --workdir D:/wake --negatives --count 1500                # похожие слова-«обманки»

Разнообразие даёт сочетание голос × скорость × высота (SSML) × варианты пунктуации. Файлы — 16 кГц, моно, 16 бит.
Для оценки и дообучения добавьте СВОИ записи (record_samples.py): синтетические примеры одного TTS-движка
дают завышенные метрики на тесте и хуже обобщаются на живой голос.
"""
import argparse
import random
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal

SPEAKERS = ["aidar", "baya", "kseniya", "xenia", "eugene"]  # голоса Silero v5_ru
RATES = ["x-slow", "slow", "medium", "fast", "x-fast"]
PITCHES = ["x-low", "low", "medium", "high", "x-high"]
SUFFIXES = ["", "", "", "!", "?", ".", "..."]               # интонация/пауза после слова

# Слова, похожие по звучанию или по смыслу, которые НЕ должны будить колонку
DEFAULT_NEGATIVES = [
    "Нил", "Нина", "Нильсен", "Нельсон", "Мильс", "Билл", "Билс", "Эльс", "Сильс", "Нигде", "Ниже", "Нить", "Нет",
    "Пилс", "Вильс", "Миля", "Ноль", "Нос", "Снимс", "Ниццы", "Лилс", "Нильсу", "Нилка", "Привет", "Алиса", "Окей",
    "Слушай", "Эй", "Музыка", "Время", "Таймер", "Будильник", "Колонка", "Ассистент", "Ноктис", "Спасибо", "Хорошо",
    "Дилс", "Тильс", "Кильс", "Фильм", "Вилка", "Мило", "Нилов", "Ниал", "Ник", "Никс",
]


def load_silero():
    import torch
    model, _ = torch.hub.load(repo_or_dir="snakers4/silero-models", model="silero_tts", language="ru",
                              speaker="v5_ru", trust_repo=True)
    torch.set_num_threads(max(1, torch.get_num_threads()))
    return model


def trim(audio: np.ndarray, thresh_db: float = -45.0, pad_ms: int = 40, sr: int = 16000) -> np.ndarray:
    """Обрезает тишину по краям (оставляет небольшой отступ)."""
    peak = float(np.max(np.abs(audio))) or 1.0
    idx = np.where(np.abs(audio) > peak * 10 ** (thresh_db / 20))[0]
    if len(idx) == 0:
        return audio
    pad = sr * pad_ms // 1000
    return audio[max(0, idx[0] - pad): min(len(audio), idx[-1] + pad)]


def synth(model, text: str, speaker: str, rate: str, pitch: str) -> np.ndarray:
    ssml = f'<speak><prosody rate="{rate}" pitch="{pitch}">{text}</prosody></speak>'
    audio = model.apply_tts(ssml_text=ssml, speaker=speaker, sample_rate=48000, put_accent=True, put_yo=True)
    audio = audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio, dtype=np.float32)
    return scipy.signal.resample_poly(audio, 1, 3).astype(np.float32)  # 48 → 16 кГц


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--phrase", default="Нильс", help="слово пробуждения (кириллицей, как произносится)")
    ap.add_argument("--negatives", action="store_true", help="синтезировать слова-обманки вместо слова пробуждения")
    ap.add_argument("--words", nargs="*", help="свои слова-обманки (по умолчанию встроенный список)")
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    out = args.workdir / ("generated_negatives" if args.negatives else "generated_samples")
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    words = (args.words or DEFAULT_NEGATIVES) if args.negatives else [args.phrase]
    model = load_silero()

    made = 0
    for i in range(args.count):
        word = rng.choice(words)
        text = word + rng.choice(SUFFIXES)
        speaker, rate, pitch = rng.choice(SPEAKERS), rng.choice(RATES), rng.choice(PITCHES)
        try:
            audio = synth(model, text, speaker, rate, pitch)
            ratio = rng.uniform(0.92, 1.08)  # лёгкая случайная растяжка: меняет темп и высоту, дубликатов нет
            audio = trim(scipy.signal.resample(audio, int(len(audio) / ratio)).astype(np.float32))
        except Exception as e:  # редкие сочетания SSML могут не пройти — просто пропускаем
            print(f"skip {text!r} {speaker}/{rate}/{pitch}: {e}")
            continue
        if len(audio) < 1600:
            continue
        wavfile.write(out / f"{i:05d}.wav", 16000, (np.clip(audio, -1, 1) * 32767).astype(np.int16))
        made += 1
        if made % 100 == 0:
            print(f"{made}/{args.count}")
    print(f"Готово: {made} файлов в {out}")


if __name__ == "__main__":
    main()
