"""Паритет микро-фронтенда: Си-код прошивки (исходники esp-micro-speech-features + параметры nww_frontend_config.h)
даёт ТЕ ЖЕ признаки, что pymicro-features, которым microwakeword считает признаки при обучении.
Запуск (в окружении обучения): python -m pytest wakeword/tests -q
"""
import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pymicro = pytest.importorskip("pymicro_features")

ROOT = Path(__file__).resolve().parents[2]
FEAT = ROOT / "firmware" / "managed_components" / "esphome__esp-micro-speech-features"
WW = ROOT / "firmware" / "components" / "noctis_wakeword"


def build_lib(tmp: Path) -> Path:
    if not FEAT.exists():
        pytest.skip("нет managed_components (соберите прошивку: pio run)")
    lib = tmp / ("nww_fe.dll" if sys.platform == "win32" else "libnww_fe.so")
    cc = [shutil.which("gcc") or shutil.which("clang")] if (shutil.which("gcc") or shutil.which("clang")) else [sys.executable, "-m", "ziglang", "cc"]
    srcs = [str(p) for p in (FEAT / "src").glob("*.c")] + [str(Path(__file__).parent / "fe_host.c")]
    subprocess.check_call(cc + ["-shared", "-O2", "-w", "-std=c11", "-I", str(FEAT / "include"), "-I", str(FEAT / "src"),
                                "-I", str(WW / "include")] + srcs + ["-o", str(lib)])
    return lib


def c_features(lib: Path, pcm: np.ndarray) -> np.ndarray:
    L = ctypes.CDLL(str(lib))
    L.nww_fe_create.restype = ctypes.c_void_p
    L.nww_fe_process.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    h = L.nww_fe_create()
    out, feats, idx = (ctypes.c_uint16 * 40)(), [], 0
    while idx + 160 < len(pcm):  # те же блоки по 160 отсчётов, что в generate_features_for_clip
        chunk = np.ascontiguousarray(pcm[idx: idx + 160])
        read = ctypes.c_size_t(0)
        n = L.nww_fe_process(h, chunk.ctypes.data, len(chunk), out, ctypes.byref(read))
        idx += read.value
        if n == 40:
            feats.append(list(out))
    L.nww_fe_destroy(ctypes.c_void_p(h))
    return np.array(feats)


def py_features(pcm: np.ndarray) -> np.ndarray:
    fe, data, idx, feats = pymicro.MicroFrontend(), pcm.tobytes(), 0, []
    while idx + 160 * 2 < len(data):
        r = fe.process_samples(data[idx: idx + 160 * 2])
        idx += r.samples_read * 2
        if r.features:
            feats.append(r.features)
    return np.array(feats)


def signals():
    rng = np.random.default_rng(0)
    t = np.arange(16000 * 2) / 16000
    yield "noise", (rng.normal(0, 3000, t.size)).astype(np.int16)
    yield "chirp", (12000 * np.sin(2 * np.pi * (200 * t + 1500 * t ** 2))).astype(np.int16)
    yield "silence", np.zeros(16000, dtype=np.int16)
    burst = np.zeros(16000 * 2)
    burst[8000:12000] = 9000 * np.sin(2 * np.pi * 440 * t[:4000])
    yield "tone burst + noise", (burst + rng.normal(0, 200, t.size)).astype(np.int16)
    wavs = sorted((ROOT.parent / "wake_train" / "generated_samples").glob("*.wav"))[:3]
    for w in wavs:
        import scipy.io.wavfile as wf
        yield f"speech {w.name}", wf.read(w)[1].astype(np.int16)


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    return build_lib(tmp_path_factory.mktemp("fe"))


@pytest.mark.parametrize("name,pcm", list(signals()))
def test_features_identical_to_training_frontend(lib, name, pcm):
    a, b = c_features(lib, pcm), py_features(pcm)
    assert a.shape == b.shape and a.shape[1] == 40 and len(a) > 5, name
    # pymicro-features отдаёт признаки уже умноженными на 1/25.6 (0.0390625) — так их видит модель при обучении
    a = a.astype(np.float32) * 0.0390625
    assert np.array_equal(a, b.astype(np.float32)), f"{name}: максимум расхождения {np.abs(a - b).max()}"
