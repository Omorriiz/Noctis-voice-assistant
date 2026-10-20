"""Контейнер модели слова пробуждения NWWM (раздел `wakeword` на плате).

Формат (little-endian), общий для сервера, инструментов обучения и прошивки (noctis_wakeword):
  0   4  магия "NWWM"
  4   2  версия формата (1)
  6   2  флаги (0)
  8   4  длина манифеста (JSON, UTF-8)
  12  4  длина модели (.tflite)
  16  32 SHA-256 от всех байт после заголовка (манифест, выравнивание, модель)
  48  .. манифест, затем выравнивание нулями до 16 байт, затем модель
Модель выровнена на 16 байт, чтобы TFLite Micro мог читать её прямо из flash (mmap).
"""
import hashlib
import json
import struct

MAGIC = b"NWWM"
VERSION = 1
HEADER = struct.Struct("<4sHHII32s")  # 48 байт
MAX_TOTAL = 1024 * 1024 - 4096         # раздел wakeword = 1 MB; запас под сектор стирания
REQUIRED_MANIFEST = ("wake_word", "feature_step_size", "probability_cutoff", "sliding_window_size",
                     "tensor_arena_size")


class ContainerError(ValueError):
    pass


def _align16(n: int) -> int:
    return (n + 15) & ~15


def pack(manifest: dict, model: bytes) -> bytes:
    mj = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode()
    body = mj + b"\x00" * (_align16(HEADER.size + len(mj)) - HEADER.size - len(mj)) + model
    # хэш — по непрерывной области после заголовка: прошивка проверяет её без копирования в RAM
    out = HEADER.pack(MAGIC, VERSION, 0, len(mj), len(model), hashlib.sha256(body).digest()) + body
    if len(out) > MAX_TOTAL:
        raise ContainerError("model container too large for the wakeword partition")
    return out


def parse(blob: bytes) -> tuple[dict, bytes]:
    """Проверяет контейнер и возвращает (манифест, модель). Бросает ContainerError."""
    if len(blob) < HEADER.size or len(blob) > MAX_TOTAL:
        raise ContainerError("bad size")
    magic, version, flags, mlen, tlen, digest = HEADER.unpack_from(blob)
    if magic != MAGIC or version != VERSION:
        raise ContainerError("bad magic or version")
    model_off = _align16(HEADER.size + mlen)
    if mlen == 0 or tlen < 16 or model_off + tlen != len(blob):
        raise ContainerError("bad lengths")
    mj = blob[HEADER.size:HEADER.size + mlen]
    model = blob[model_off:model_off + tlen]
    if hashlib.sha256(blob[HEADER.size:]).digest() != digest:
        raise ContainerError("checksum mismatch")
    if model[4:8] != b"TFL3":  # идентификатор файла TFLite (flatbuffer)
        raise ContainerError("not a TFLite model")
    try:
        manifest = json.loads(mj)
    except ValueError as e:
        raise ContainerError(f"bad manifest: {e}")
    missing = [k for k in REQUIRED_MANIFEST if k not in manifest]
    if missing:
        raise ContainerError(f"manifest lacks: {', '.join(missing)}")
    if manifest["feature_step_size"] != 10:
        raise ContainerError("only 10 ms feature step is supported")
    if not (0.0 < float(manifest["probability_cutoff"]) <= 1.0) or not (1 <= int(manifest["sliding_window_size"]) <= 20):
        raise ContainerError("bad detection parameters")
    return manifest, model
