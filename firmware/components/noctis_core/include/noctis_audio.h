/**
 * Noctis — буфер и состояние музыкального плеера платы (этап 5). Портативный Си.
 *
 * Музыку декодирует сервер (MP3/FLAC/Vorbis/WAV → PCM16 16 кГц моно) и шлёт сообщениями:
 *   music_start {title, artist, source, index, total}  — сбросить буфер, накопить предзапас
 *   music_chunk {index, data(base64)}                   — 3200 байт = 100 мс
 *   music_end {reason} / music_pause / music_resume / music_stop
 * Плеер складывает данные в кольцевой буфер (в PSRAM), а задача воспроизведения забирает их через noctis_player_pull,
 * когда устройство не занято голосовым диалогом.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "noctis_core.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---------- кольцевой буфер байтов (память выдаёт вызывающий) ---------- */
typedef struct {
    uint8_t* buf;
    size_t cap, head, size;
} noctis_ring_t;

NOCTIS_API void noctis_ring_init(noctis_ring_t* r, uint8_t* storage, size_t cap);
NOCTIS_API size_t noctis_ring_write(noctis_ring_t* r, const uint8_t* data, size_t len);  // сколько записано (по месту)
NOCTIS_API size_t noctis_ring_read(noctis_ring_t* r, uint8_t* out, size_t max);          // сколько прочитано
NOCTIS_API void noctis_ring_clear(noctis_ring_t* r);
NOCTIS_API size_t noctis_ring_size(const noctis_ring_t* r);
NOCTIS_API size_t noctis_ring_free(const noctis_ring_t* r);

/* ---------- base64 ---------- */
/** Декодирует base64 (без переводов строк). false — неверный символ/длина или не помещается в cap. */
NOCTIS_API bool noctis_base64_decode(const char* in, size_t in_len, uint8_t* out, size_t cap, size_t* out_len);

/* ---------- плеер ---------- */
#define PLAYER_PREBUFFER_BYTES 8000        // 250 мс — накопить перед стартом и после опустошения
#define PLAYER_TITLE_MAX 48

typedef enum {
    PLAYER_IDLE = 0,
    PLAYER_BUFFERING = 1,
    PLAYER_PLAYING = 2,
    PLAYER_PAUSED = 3,
} player_state_t;

typedef struct {
    noctis_ring_t ring;
    player_state_t state;
    bool ended;                        // сервер сообщил, что трек закончился (доиграть остаток)
    bool paused;
    char title[PLAYER_TITLE_MAX];
    char artist[PLAYER_TITLE_MAX];
    char source[10];
    uint32_t overruns;                 // сколько байт не поместилось (буфер переполнен)
    uint32_t underruns;                // сколько раз данные кончились посреди трека
    uint32_t received_bytes;
    size_t prebuffer;                  // сколько байт накопить перед стартом и после опустошения (по умолчанию PLAYER_PREBUFFER_BYTES)
} noctis_player_t;

typedef enum {
    PLAYER_MSG_NONE = 0,               // не музыкальное сообщение или игнорировано
    PLAYER_MSG_START,
    PLAYER_MSG_CHUNK,
    PLAYER_MSG_END,
    PLAYER_MSG_PAUSE,
    PLAYER_MSG_RESUME,
    PLAYER_MSG_STOP,
} player_msg_t;

NOCTIS_API void noctis_player_init(noctis_player_t* p, uint8_t* storage, size_t cap);
/** Предзапас перед стартом: больше — устойчивее к паузам Wi-Fi (но не больше ёмкости буфера; чётное число байт). */
NOCTIS_API void noctis_player_set_prebuffer(noctis_player_t* p, size_t bytes);
NOCTIS_API size_t noctis_player_size(void);   /* sizeof(noctis_player_t) — проверка раскладки в хост-тестах */
/** Применяет сообщение сервера music_*; возвращает вид сообщения (PLAYER_MSG_NONE — игнорировано/неверно). */
NOCTIS_API player_msg_t noctis_player_message(noctis_player_t* p, const char* json, size_t len);
/** Забрать данные для воспроизведения (чётное число байт ≤ max). 0 — пока играть нечего (пауза, предзапас, конец). */
NOCTIS_API size_t noctis_player_pull(noctis_player_t* p, uint8_t* out, size_t max);
/** Принудительно остановить и очистить (например, начался диалог или зазвонил будильник). */
NOCTIS_API void noctis_player_stop(noctis_player_t* p);
NOCTIS_API bool noctis_player_active(const noctis_player_t* p);   // идёт трек (не IDLE)

/** Усиление музыки относительно общей громкости (в процентах, 0..100) → множитель Q8. */
NOCTIS_API int noctis_music_gain_q8(int volume_percent, int music_percent);
/** Применяет множитель Q8 к PCM16 (на месте), с ограничением. */
NOCTIS_API void noctis_apply_gain(int16_t* samples, size_t count, int gain_q8);

#ifdef __cplusplus
}
#endif
