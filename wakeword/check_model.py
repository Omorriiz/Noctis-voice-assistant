"""Проверка, что обученная модель поддерживается прошивкой: набор операций TFLite, форма и тип входа/выхода.

  python check_model.py stream_state_internal_quant.tflite

Набор поддерживаемых операций берётся прямо из firmware/components/noctis_wakeword/nww_detector.cpp (register_ops),
поэтому проверка не расходится с прошивкой.
"""
import re
import sys
from pathlib import Path

DETECTOR = Path(__file__).resolve().parents[1] / "firmware/components/noctis_wakeword/nww_detector.cpp"
NAMES = {  # Add<Имя> в MicroMutableOpResolver -> имя оператора в схеме TFLite
    "CallOnce": "CALL_ONCE", "VarHandle": "VAR_HANDLE", "Reshape": "RESHAPE", "ReadVariable": "READ_VARIABLE",
    "StridedSlice": "STRIDED_SLICE", "Concatenation": "CONCATENATION", "AssignVariable": "ASSIGN_VARIABLE",
    "Conv2D": "CONV_2D", "Mul": "MUL", "Add": "ADD", "Mean": "MEAN", "FullyConnected": "FULLY_CONNECTED",
    "Logistic": "LOGISTIC", "Quantize": "QUANTIZE", "DepthwiseConv2D": "DEPTHWISE_CONV_2D",
    "AveragePool2D": "AVERAGE_POOL_2D", "MaxPool2D": "MAX_POOL_2D", "Pad": "PAD", "Pack": "PACK", "SplitV": "SPLIT_V",
}


def registered_ops() -> set[str]:
    src = DETECTOR.read_text(encoding="utf-8")
    body = src[src.index("bool register_ops"):src.index("struct Detector")]
    found = re.findall(r"\.Add(\w+)\(\)", body)
    unknown = [f for f in found if f not in NAMES]
    if unknown:
        raise SystemExit(f"Добавьте в NAMES соответствие для: {unknown}")
    return {NAMES[f] for f in found}


def inspect(model_path: Path) -> dict:
    from tensorflow.lite.python import schema_py_generated as schema
    import tensorflow as tf
    buf = bytearray(model_path.read_bytes())
    model = schema.Model.GetRootAsModel(buf, 0)
    builtin = {v: k for k, v in vars(schema.BuiltinOperator).items() if isinstance(v, int)}
    ops = set()
    for i in range(model.OperatorCodesLength()):
        code = model.OperatorCodes(i)
        c = max(code.BuiltinCode(), code.DeprecatedBuiltinCode())
        ops.add(builtin.get(c, f"UNKNOWN_{c}") if c != schema.BuiltinOperator.CUSTOM else f"CUSTOM:{code.CustomCode()}")
    interp = tf.lite.Interpreter(model_content=bytes(buf))
    interp.allocate_tensors()
    inp, out = interp.get_input_details()[0], interp.get_output_details()[0]
    return {"ops": ops, "input_shape": list(inp["shape"]), "input_dtype": inp["dtype"].__name__,
            "input_scale": inp["quantization"][0], "input_zero": inp["quantization"][1],
            "output_dtype": out["dtype"].__name__, "output_quant": out["quantization"], "size": len(buf)}


def check(model_path: Path) -> list[str]:
    """Возвращает список проблем (пусто — модель подходит)."""
    info, problems = inspect(model_path), []
    unsupported = info["ops"] - registered_ops()
    if unsupported:
        problems.append(f"операции не зарегистрированы в прошивке: {sorted(unsupported)}")
    if info["input_dtype"] != "int8" or len(info["input_shape"]) != 3 or info["input_shape"][2] != 40:
        problems.append(f"вход должен быть int8 [1,N,40], а не {info['input_dtype']} {info['input_shape']}")
    return problems


if __name__ == "__main__":
    path = Path(sys.argv[1])
    print(inspect(path))
    issues = check(path)
    print("OK: модель поддерживается прошивкой" if not issues else "\n".join("ПРОБЛЕМА: " + i for i in issues))
    sys.exit(1 if issues else 0)
