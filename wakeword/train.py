"""Обучение модели слова пробуждения (MixedNet, потоковый int8) на CPU/GPU, нативно на Windows.

  python train.py --workdir D:/wake --steps 10000

Пишет training_parameters.yaml и запускает microwakeword.model_train_eval; в конце обучение квантует модель
и печатает метрики на тестовых наборах (полнота и ложные срабатывания в час по порогам).
Результат: <workdir>/trained_models/wakeword/tflite_stream_state_internal_quant/stream_state_internal_quant.tflite
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml


def build_config(work: Path, steps: int, neg_weight: int, with_adversarial: bool) -> dict:
    feats = [
        {"features_dir": str(work / "generated_augmented_features"), "sampling_weight": 2.0, "penalty_weight": 1.0,
         "truth": True, "truncation_strategy": "truncate_start", "type": "mmap"},
        {"features_dir": str(work / "negative_datasets/speech"), "sampling_weight": 10.0, "penalty_weight": 1.0,
         "truth": False, "truncation_strategy": "random", "type": "mmap"},
        {"features_dir": str(work / "negative_datasets/dinner_party"), "sampling_weight": 10.0, "penalty_weight": 1.0,
         "truth": False, "truncation_strategy": "random", "type": "mmap"},
        {"features_dir": str(work / "negative_datasets/no_speech"), "sampling_weight": 5.0, "penalty_weight": 1.0,
         "truth": False, "truncation_strategy": "random", "type": "mmap"},
        {"features_dir": str(work / "negative_datasets/dinner_party_eval"), "sampling_weight": 0.0,
         "penalty_weight": 1.0, "truth": False, "truncation_strategy": "split", "type": "mmap"},  # только проверка
    ]
    if with_adversarial:  # слова, похожие на «Нильс», — главный источник ложных срабатываний у короткого слова
        feats.insert(1, {"features_dir": str(work / "adversarial_features"), "sampling_weight": 4.0,
                         "penalty_weight": 1.0, "truth": False, "truncation_strategy": "truncate_start", "type": "mmap"})
    return {
        "window_step_ms": 10, "train_dir": str(work / "trained_models/wakeword"), "features": feats,
        "training_steps": [steps], "positive_class_weight": [1], "negative_class_weight": [neg_weight],
        "learning_rates": [0.001], "batch_size": 128,
        "time_mask_max_size": [0], "time_mask_count": [0], "freq_mask_max_size": [0], "freq_mask_count": [0],
        "eval_step_interval": 500, "clip_duration_ms": 1500, "target_minimization": 0.9,
        "minimization_metric": None, "maximization_metric": "average_viable_recall",
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # консоль Windows (cp1251) не должна ронять обучение
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--steps", type=int, default=10000)
    ap.add_argument("--negative-weight", type=int, default=20, help="больше — меньше ложных срабатываний, но ниже полнота")
    args = ap.parse_args()
    work = args.workdir

    cfg = build_config(work, args.steps, args.negative_weight, (work / "adversarial_features").exists())
    cfg_path = work / "training_parameters.yaml"
    cfg_path.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")

    cmd = [sys.executable, "-m", "microwakeword.model_train_eval", f"--training_config={cfg_path}", "--train", "1",
           "--restore_checkpoint", "1", "--test_tf_nonstreaming", "0", "--test_tflite_nonstreaming", "0",
           "--test_tflite_nonstreaming_quantized", "0", "--test_tflite_streaming", "0",
           "--test_tflite_streaming_quantized", "1", "--use_weights", "best_weights", "mixednet",
           "--pointwise_filters", "64,64,64,64", "--repeat_in_block", "1, 1, 1, 1",
           "--mixconv_kernel_sizes", "[5], [7,11], [9,15], [23]", "--residual_connection", "0,0,0,0",
           "--first_conv_filters", "32", "--first_conv_kernel_size", "5", "--stride", "3"]
    print(" ".join(cmd))
    with open(work / "train.log", "a", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", cwd=work, env=dict(os.environ, PYTHONUTF8="1", TF_CPP_MIN_LOG_LEVEL="1"))
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
        sys.exit(proc.wait())


if __name__ == "__main__":
    main()
