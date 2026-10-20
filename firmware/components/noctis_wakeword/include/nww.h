/**
 * Детектор слова пробуждения microWakeWord (TFLite Micro) для ESP32-S3.
 *
 * Конвейер: PCM 16 кГц → микро-фронтенд (40 mel-признаков, окно 30 мс, шаг 10 мс, шумоподавление, АРУ) →
 * потоковая int8-модель (состояние в переменных TFLM) → скользящее среднее вероятностей → срабатывание.
 * Параметры фронтенда ДОЛЖНЫ совпадать с обучением (microwakeword использует те же значения).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "noctis_core.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Создаёт фронтенд и интерпретатор для модели из контейнера (указатели внутри контейнера должны жить, пока работает детектор). */
bool nww_start(const nww_container_t* container);

/** Подаёт отсчёты PCM16 моно 16 кГц. true — слово обнаружено (флаг держится до nww_reset). Потокобезопасно. */
bool nww_feed(const int16_t* pcm, size_t count);

/** Сбрасывает окно вероятностей и состояние модели (вызывать перед новым ожиданием слова). */
void nww_reset(void);

float nww_last_probability(void);
void nww_stop(void);

/** Раздел "wakeword": отображает во flash, проверяет контейнер; installed_sha_hex — SHA-256 всего контейнера
 *  (пустая строка, если модели нет) — сервер сверяет его с хэшем в config. */
bool nww_partition_load(nww_container_t* container, char installed_sha_hex[65]);

/** Записывает контейнер в раздел (стирание + запись + проверка чтением). Контейнер должен быть уже проверен. */
bool nww_partition_write(const uint8_t* blob, size_t len);

#ifdef __cplusplus
}
#endif
