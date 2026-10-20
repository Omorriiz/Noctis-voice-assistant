/**
 * Noctis — расписание на плате (этап 5): таймеры, будильники, время, мелодии. Портативный Си (без ESP-IDF),
 * проверяется хост-тестами и через виртуальную колонку.
 *
 * Сервер присылает снимок `schedule` (полный список) при подключении и при каждом изменении; плата хранит его в NVS
 * (sched_serialize/deserialize с CRC32) и срабатывает сама по времени, поэтому будильник звонит и без связи с сервером.
 * Срабатывание запоминается (fired / last_key), чтобы не повторяться после перезагрузки или нового снимка.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "noctis_core.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SCHED_MAX_TIMERS 8
#define SCHED_MAX_ALARMS 16
#define SCHED_LABEL_MAX 24
#define SCHED_MELODY_MAX 12
#define SCHED_BLOB_MAX 1200

typedef struct {
    int32_t id;
    int64_t end_epoch;               // UTC, секунды
    bool fired;
    char label[SCHED_LABEL_MAX];
} sched_timer_t;

typedef struct {
    int32_t id;
    uint8_t hour, minute;
    uint8_t weekdays;                // маска: бит 0 = понедельник ... бит 6 = воскресенье; 0 = однократный
    int32_t date_ymd;                // у однократного: ГГГГММДД (местная дата), иначе 0
    int32_t last_key;                // минута последнего звонка (местное время / 60); 0 — не звонил
    char label[SCHED_LABEL_MAX];
    char melody[SCHED_MELODY_MAX];
} sched_alarm_t;

typedef struct {
    int32_t rev;
    int n_timers, n_alarms;
    sched_timer_t timers[SCHED_MAX_TIMERS];
    sched_alarm_t alarms[SCHED_MAX_ALARMS];
} sched_state_t;

typedef enum { SCHED_EVT_TIMER = 1, SCHED_EVT_ALARM = 2 } sched_evt_kind_t;

typedef struct {
    sched_evt_kind_t kind;
    int32_t id;
    char label[SCHED_LABEL_MAX];
    char melody[SCHED_MELODY_MAX];   // у таймера — "default"
} sched_event_t;

NOCTIS_API void sched_init(sched_state_t* s);
/** sizeof(sched_state_t) — для проверки раскладки структур в хост-тестах через ctypes. */
NOCTIS_API size_t sched_state_size(void);

/** Разбирает сообщение {"type":"schedule",...}. Состояние fired/last_key элементов с теми же id берётся из prev
 *  (может быть NULL). false — не тот тип или неверный JSON (out не меняется). Неверные элементы пропускаются. */
NOCTIS_API bool sched_parse(const char* json, size_t len, const sched_state_t* prev, sched_state_t* out);

/** Что сработало к моменту now_epoch (UTC) при смещении utc_offset (сек). Помечает сработавшее. Пока время
 *  не синхронизировано (noctis_time_valid == false) ничего не срабатывает. Возвращает число событий (≤ max). */
NOCTIS_API int sched_poll(sched_state_t* s, int64_t now_epoch, int32_t utc_offset, sched_event_t* out, int max);

/** Для экрана: секунд до ближайшего таймера (-1 — нет) и ближайший будильник в ближайшие 7 суток (hour = -1 — нет). */
NOCTIS_API void sched_summary(const sched_state_t* s, int64_t now_epoch, int32_t utc_offset, int* timer_left_sec,
                              int* alarm_hour, int* alarm_minute);

/** Хранение: двоичный блок "NSCH" + версия + данные + CRC32. Возвращает размер или 0, если не помещается. */
NOCTIS_API size_t sched_serialize(const sched_state_t* s, uint8_t* buf, size_t cap);
/** false при неверной магии/версии/CRC/длине или значениях вне диапазона (out не меняется). */
NOCTIS_API bool sched_deserialize(const uint8_t* buf, size_t len, sched_state_t* out);

/** {"type":"sched_event","kind":"timer"|"alarm","id":N}; длина или -1. */
NOCTIS_API int sched_build_event(char* out, size_t cap, sched_evt_kind_t kind, int32_t id);
/** {"type":"ring_stopped"}; длина или -1. */
NOCTIS_API int sched_build_ring_stopped(char* out, size_t cap);

/* ---------- время ---------- */
/** Время считается синхронизированным, если не раньше 2024-01-01 (до NTP у платы без RTC — 1970 год). */
NOCTIS_API bool noctis_time_valid(int64_t epoch);
/** {"type":"time_sync","epoch":N,"utc_offset":S}: true, если разобрано и значения правдоподобны (|offset| ≤ 14 ч). */
NOCTIS_API bool noctis_parse_time_sync(const char* json, size_t len, int64_t* epoch, int32_t* utc_offset);

typedef struct {
    int year, month, day;            // 1..
    int hour, minute, second;
    int wday;                        // 0 = понедельник
} noctis_civil_t;
/** Календарная дата/время из секунд (уже со смещением пояса). */
NOCTIS_API void noctis_civil(int64_t local_epoch, noctis_civil_t* out);
/** "HH:MM" для времени epoch (UTC) в поясе utc_offset. Возвращает длину или -1. */
NOCTIS_API int noctis_format_clock(int64_t epoch, int32_t utc_offset, char* out, size_t cap);

/* ---------- мелодии будильника/таймера ---------- */
/** Нота step мелодии: частота (0 = пауза) и длительность в мс. 1 — нота есть, 0 — мелодия (паттерн) закончилась;
 *  прошивка проигрывает паттерн по кругу до остановки. Неизвестное имя = "default". */
NOCTIS_API int noctis_melody_note(const char* melody, int step, int* freq_hz, int* ms);
NOCTIS_API bool noctis_is_valid_melody(const char* melody);

#ifdef __cplusplus
}
#endif
