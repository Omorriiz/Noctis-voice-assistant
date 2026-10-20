"""Скачивание и подготовка данных для обучения (нативно на Windows, без wget/tar/unzip).

Источники (лицензии смешанные → полученная модель только для некоммерческого/личного использования):
  * готовые признаки негативных наборов microWakeWord (HF kahrendt/microwakeword): речь, ужин, тишина/шум ≈ 6 ГБ;
  * MIT environmental impulse responses (реверберация);
  * AudioSet (одна часть, parquet) и Free Music Archive xs — фоновый шум для аугментации.
Скачивание докачивается (Range), повторный запуск безопасен.

  python prepare_data.py --workdir D:/wake [--skip-negatives] [--noise-clips 1500]
"""
import argparse
import io
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal

HF = "https://huggingface.co/datasets/"
NEG_ROOT = HF + "kahrendt/microwakeword/resolve/main/"
NEGATIVES = ["dinner_party_eval", "dinner_party", "no_speech", "speech"]
FMA = HF + "mchl914/fma_xsmall/resolve/main/fma_xs.zip"
AUDIOSET = HF + "agkphysics/AudioSet/resolve/main/data/bal_train/09.parquet"


def download(url: str, dest: Path):
    """Скачивание с докачкой. Используется curl (есть в Windows 10+): он надёжнее urllib на редиректах Hugging Face."""
    import shutil
    import subprocess
    dest.parent.mkdir(parents=True, exist_ok=True)
    curl = shutil.which("curl")
    if curl:
        resume = ["-C", "-"] if dest.exists() and dest.stat().st_size > 0 else []  # без файла Range не отправляем
        rc = subprocess.call([curl, "-sSL", *resume, "--retry", "5", "--retry-delay", "3", "--connect-timeout", "30",
                              "--max-time", "3600", "-o", str(dest), url])
        if rc not in (0, 33):  # 33 — файл уже докачан целиком
            raise RuntimeError(f"curl: код {rc} для {url}")
    else:
        urllib.request.urlretrieve(url, dest)
    print("  скачано:", dest.name, dest.stat().st_size // 2**20, "МБ")


def to_16k_int16(x: np.ndarray, sr: int) -> np.ndarray:
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != 16000:
        x = scipy.signal.resample_poly(x, 16000, sr)
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def prepare_negatives(work: Path):
    neg = work / "negative_datasets"
    for name in NEGATIVES:
        if (neg / name).exists():
            continue
        z = work / "data" / f"{name}.zip"
        print("Негативный набор:", name)
        download(NEG_ROOT + f"{name}.zip", z)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(neg)


def prepare_rirs(work: Path):
    """MIT impulse responses: .wav-файлы (уже 16 кГц) качаются напрямую с Hugging Face, без datasets/torchcodec."""
    import json
    out = work / "mit_rirs"
    out.mkdir(exist_ok=True)  # повторный запуск докачивает недостающее (готовые файлы пропускаются)
    api = HF.replace("datasets/", "api/datasets/") + "davidscripka/MIT_environmental_impulse_responses/tree/main/16khz"
    files = None
    for attempt in range(5):  # API Hugging Face иногда отвечает таймаутом
        try:
            files = json.loads(urllib.request.urlopen(api, timeout=120).read())
            break
        except Exception as e:
            print("  список RIR: повтор", attempt + 1, e)
    if files is None:
        raise RuntimeError("не удалось получить список файлов MIT RIR")
    for item in files:
        target = out / Path(item["path"]).name
        if item["path"].endswith(".wav") and not (target.exists() and target.stat().st_size == item.get("size")):
            download(HF + "davidscripka/MIT_environmental_impulse_responses/resolve/main/" + item["path"],
                     target)
    print("MIT RIR:", len(list(out.glob("*.wav"))))


def prepare_audioset(work: Path, limit: int):
    out = work / "audioset_16k"
    if out.exists():
        return
    import pyarrow.parquet as pq
    import soundfile as sf
    pqf = work / "data" / "audioset_09.parquet"
    print("AudioSet (parquet)…")
    download(AUDIOSET, pqf)
    out.mkdir()
    table = pq.ParquetFile(pqf)
    n = 0
    for batch in table.iter_batches(batch_size=64, columns=["audio"]):
        for item in batch.to_pylist():
            data, sr = sf.read(io.BytesIO(item["audio"]["bytes"]), dtype="float32")
            wavfile.write(out / f"as_{n:05d}.wav", 16000, to_16k_int16(data, sr))
            n += 1
            if n >= limit:
                return


def prepare_fma(work: Path, limit: int):
    out = work / "fma_16k"
    if out.exists():
        return
    import soundfile as sf
    z = work / "data" / "fma_xs.zip"
    print("FMA xs…")
    download(FMA, z)
    out.mkdir()
    n = 0
    with zipfile.ZipFile(z) as zf:
        for name in zf.namelist():
            if not name.lower().endswith((".mp3", ".wav", ".flac")):
                continue
            try:
                data, sr = sf.read(io.BytesIO(zf.read(name)), dtype="float32")  # mp3 поддерживает libsndfile ≥1.1
            except Exception:
                continue
            wavfile.write(out / f"fma_{n:05d}.wav", 16000, to_16k_int16(data, sr))
            n += 1
            if n >= limit:
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--skip-negatives", action="store_true")
    ap.add_argument("--noise-clips", type=int, default=1500, help="сколько клипов шума брать из AudioSet и FMA")
    args = ap.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    if not args.skip_negatives:
        prepare_negatives(args.workdir)
    prepare_rirs(args.workdir)
    prepare_audioset(args.workdir, args.noise_clips)
    prepare_fma(args.workdir, args.noise_clips)
    print("Данные готовы.")


if __name__ == "__main__":
    main()
