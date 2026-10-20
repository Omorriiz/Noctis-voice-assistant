/**
 * Параметры микро-фронтенда TFLM для microWakeWord. ДОЛЖНЫ совпадать с обучением: microwakeword считает признаки
 * через pymicro-features с этими значениями (проверка — wakeword/tests/test_frontend_parity.py, побитово).
 * Файл общий для прошивки (nww_detector.cpp) и хост-теста.
 */
#pragma once

#include "frontend.h"
#include "frontend_util.h"

#define NWW_FEATURE_SIZE 40

static inline void nww_frontend_fill_config(struct FrontendConfig* cfg) {
    cfg->window.size_ms = 30;
    cfg->window.step_size_ms = 10;
    cfg->filterbank.num_channels = NWW_FEATURE_SIZE;
    cfg->filterbank.lower_band_limit = 125.0f;
    cfg->filterbank.upper_band_limit = 7500.0f;
    cfg->noise_reduction.smoothing_bits = 10;
    cfg->noise_reduction.even_smoothing = 0.025f;
    cfg->noise_reduction.odd_smoothing = 0.06f;
    cfg->noise_reduction.min_signal_remaining = 0.05f;
    cfg->pcan_gain_control.enable_pcan = 1;
    cfg->pcan_gain_control.strength = 0.95f;
    cfg->pcan_gain_control.offset = 80.0f;
    cfg->pcan_gain_control.gain_bits = 21;
    cfg->log_scale.enable_log = 1;
    cfg->log_scale.scale_shift = 6;
}
