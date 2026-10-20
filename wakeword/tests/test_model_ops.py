"""Модели microWakeWord используют только операции, зарегистрированные в прошивке (nww_detector.cpp)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("tensorflow")
import check_model  # noqa: E402

WORK = Path(__file__).resolve().parents[3] / "wake_train"
CANDIDATES = [WORK / "ref" / "okay_nabu.tflite",
              WORK / "trained_models/wakeword/tflite_stream_state_internal_quant/stream_state_internal_quant.tflite"]


@pytest.mark.parametrize("path", [p for p in CANDIDATES if p.exists()] or [pytest.param(None, marks=pytest.mark.skip("нет моделей"))])
def test_model_supported_by_firmware(path):
    info = check_model.inspect(path)
    assert info["input_shape"][0] == 1 and info["input_shape"][2] == 40, info
    assert check_model.check(path) == [], info["ops"]


def test_registered_ops_parsed_from_firmware_source():
    ops = check_model.registered_ops()
    assert {"CALL_ONCE", "VAR_HANDLE", "READ_VARIABLE", "ASSIGN_VARIABLE", "CONV_2D", "STRIDED_SLICE"} <= ops
