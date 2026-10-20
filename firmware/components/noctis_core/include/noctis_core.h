/**
 * Noctis — переносимая логика прошивки (без ESP-IDF): собирается и на ПК для хост-тестов.
 * Здесь: настройки устройства и их проверка, разбор сообщений сервера, версии, SHA-256 (hex), URI.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32) && defined(NOCTIS_SHARED)
#define NOCTIS_API __declspec(dllexport)  /* только для сборки библиотеки хост-тестов */
#else
#define NOCTIS_API
#endif

#define NOCTIS_FIRMWARE_VERSION "0.4.0"

// Допустимые значения (совпадают с server/noctis_server/catalog.py)
#define NOCTIS_WAKE_WORD_MAX 64
#define NOCTIS_TZ_MAX 64
#define NOCTIS_SERVER_MAX 96
#define NOCTIS_ID_MAX 32
#define NOCTIS_TOKEN_MAX 64
#define NOCTIS_URL_MAX 256

typedef struct {
    char wake_word[NOCTIS_WAKE_WORD_MAX];  // "nils" по умолчанию
    char voice_gender[8];                  // "male" | "female"
    char character_id[16];                 // один из 10 характеров
    char language[3];                      // ru en es de fr
    char timezone[NOCTIS_TZ_MAX];
    int volume;                            // 0..100
    int revision;                          // версия настроек на сервере
} noctis_settings_t;

typedef struct {
    char server[NOCTIS_SERVER_MAX];     // "192.168.1.5:8000" или "noctis.example.ru"
    bool tls;                           // wss:// вместо ws://
    char device_id[NOCTIS_ID_MAX];
    char token[NOCTIS_TOKEN_MAX];
} noctis_link_t;

typedef enum {
    NOCTIS_CMD_NONE = 0,
    NOCTIS_CMD_REBOOT,
    NOCTIS_CMD_FACTORY_RESET,
    NOCTIS_CMD_OTA,
    NOCTIS_CMD_STOP_RING,  // остановить звонок будильника/таймера
    NOCTIS_CMD_SET_WIFI,   // перейти в другую сеть Wi-Fi (с откатом на прежнюю при неудаче)
} noctis_cmd_t;

#define NOCTIS_SSID_MAX 33
#define NOCTIS_WIFI_PASS_MAX 65
#define NOCTIS_REQID_MAX 17

typedef struct {
    noctis_cmd_t cmd;
    char version[16];
    char sha256[65];
    int size;
    char ssid[NOCTIS_SSID_MAX];           // только для SET_WIFI
    char password[NOCTIS_WIFI_PASS_MAX];  // только для SET_WIFI; не логируется
    char request_id[NOCTIS_REQID_MAX];    // эхо в wifi_result
} noctis_command_t;

typedef enum {
    NOCTIS_MSG_OTHER = 0,
    NOCTIS_MSG_CONFIG,
    NOCTIS_MSG_COMMAND,
} noctis_msg_kind_t;

NOCTIS_API void noctis_settings_defaults(noctis_settings_t* s);

/** Проверка и нормализация: исправляет значения вне диапазона, возвращает число исправлений. */
NOCTIS_API int noctis_settings_sanitize(noctis_settings_t* s);

NOCTIS_API bool noctis_is_valid_language(const char* v);
NOCTIS_API bool noctis_is_valid_voice(const char* v);
NOCTIS_API bool noctis_is_valid_character(const char* v);

/** Слово пробуждения: id пресета (nils, privet_esp, hi_esp, hallo_esp, hola_esp, bonjour_esp, alexa, computer,
 * jarvis) или "custom:<фраза>" (2-32 символа: буквы, пробел, дефис внутри). Правила совпадают с сервером
 * (catalog.is_valid_wake_word); прошивка не проверяет, что символ ≥0x80 — буква (сервер строже). */
NOCTIS_API bool noctis_is_valid_wake_word(const char* v);

/**
 * Разбирает JSON-сообщение сервера. Для "config" заполняет *settings (поля, которых нет в
 * сообщении, остаются прежними) и возвращает NOCTIS_MSG_CONFIG; для "command" заполняет *cmd.
 * Некорректные значения отбрасываются (поле не меняется). NOCTIS_MSG_OTHER — прочие типы / ошибка.
 */
NOCTIS_API noctis_msg_kind_t noctis_parse_server_message(const char* json, size_t len, noctis_settings_t* settings,
                                              noctis_command_t* cmd);

/** Разбор JSON от приложения при BLE-сопряжении: {"server","tls","device_id","token"}. */
NOCTIS_API bool noctis_parse_link(const char* json, size_t len, noctis_link_t* out);

/**
 * Повторная настройка по Bluetooth без сброса платы: {"server","tls"} и, необязательно, "device_id"+"token".
 * Если device_id и token не присланы, сохраняются прежние (нужна уже существующая связь: have_existing).
 * *link — и вход (текущая связь), и выход; при false не изменяется.
 */
NOCTIS_API bool noctis_parse_link_update(const char* json, size_t len, noctis_link_t* link, bool have_existing);

/** ws[s]://server/noctis/v1/ (без секретов в URL). Возвращает длину или -1, если не помещается. */
NOCTIS_API int noctis_build_ws_uri(const noctis_link_t* link, char* out, size_t cap);

/** "X-Device-Id: ..\r\nX-Device-Token: ..\r\n" — секрет передаётся заголовком, а не в URL (не попадает в логи). */
NOCTIS_API int noctis_build_auth_headers(const noctis_link_t* link, char* out, size_t cap);

/** http[s]://server/api/v1/device/ota/<version> — адрес образа на сервере. */
NOCTIS_API int noctis_build_ota_url(const noctis_link_t* link, const char* version, char* out, size_t cap);

/** Сообщение device_info (JSON). Возвращает длину или -1. */
NOCTIS_API int noctis_build_device_info(char* out, size_t cap, int config_revision, unsigned free_heap,
                             const char* reset_reason);

/** Сообщение ota_status (JSON). state: "started" | "ok" | "failed". */
NOCTIS_API int noctis_build_ota_status(char* out, size_t cap, const char* version, const char* state,
                            const char* error);

/** Сравнение "1.2.3": <0, 0, >0; -2 при неверном формате. */
NOCTIS_API int noctis_version_compare(const char* a, const char* b);

/** 64 hex-символа SHA-256 → 32 байта; false при неверном формате. Регистр не важен. */
NOCTIS_API bool noctis_sha256_from_hex(const char* hex, uint8_t out[32]);
NOCTIS_API void noctis_sha256_to_hex(const uint8_t in[32], char out[65]);

/** Безопасная копия строки с гарантированным нулём; false, если пришлось обрезать. */
NOCTIS_API bool noctis_copy(char* dst, size_t cap, const char* src);

/* ============ Этап 4: слово пробуждения (microWakeWord) ============ */

/** SHA-256 (переносимая реализация: нужна и на ПК для хост-тестов). */
NOCTIS_API void noctis_sha256(const uint8_t* data, size_t len, uint8_t out[32]);

typedef enum {
    NOCTIS_ENGINE_ESP_SR = 0,  // готовая модель Espressif WakeNet (раздел model)
    NOCTIS_ENGINE_MWW = 1,     // microWakeWord: модель из раздела wakeword (nils, custom:*)
} noctis_engine_t;

/** Какой движок обслуживает слово. Для ESP_SR в *esp_sr_model — подстрока имени модели esp-sr
 *  (для esp_srmodel_filter), иначе NULL. Неизвестное слово → «Привет ESP» (запасной вариант). */
NOCTIS_API noctis_engine_t noctis_wake_engine(const char* wake_word, const char** esp_sr_model);

/** Разбор поля "wake_model":{"sha256","size"} из сообщения config. false — поля нет/null/некорректно. */
NOCTIS_API bool noctis_parse_wake_model(const char* json, size_t len, char sha256_hex[65], int* size);

/** Нужно ли скачать модель: слово обслуживает microWakeWord, сервер прислал хэш и он не совпадает с
 *  установленным (installed_hex может быть пустым). */
NOCTIS_API bool noctis_wake_model_needed(const char* wake_word, const char* installed_hex, const char* server_hex);

/** http[s]://server/api/v1/device/wakeword/<sha256> */
NOCTIS_API int noctis_build_wakeword_url(const noctis_link_t* link, const char* sha256_hex, char* out, size_t cap);

#define NWWM_HEADER_SIZE 48
#define NWWM_MAX_TOTAL (1024 * 1024 - 4096)

typedef struct {
    char wake_word[NOCTIS_WAKE_WORD_MAX];
    char label[NOCTIS_WAKE_WORD_MAX];
    int feature_step_size;       // мс, поддерживается только 10
    float probability_cutoff;    // 0..1
    int sliding_window_size;     // 1..20
    int tensor_arena_size;       // байт
} nww_manifest_t;

typedef struct {
    nww_manifest_t manifest;
    const uint8_t* model;        // указывает внутрь исходного буфера (без копирования)
    size_t model_len;
    size_t total_len;            // размер всего контейнера
    uint8_t sha256[32];          // хэш от (манифест || модель), записан в заголовке
} nww_container_t;

/** Проверяет контейнер NWWM (магия, длины, SHA-256, идентификатор TFLite, манифест). Формат: server/noctis_server/nwwm.py.
 *  avail — сколько байт доступно для чтения (размер раздела). При ошибке *err — короткое описание. */
NOCTIS_API bool nww_container_parse(const uint8_t* blob, size_t avail, nww_container_t* out, const char** err);

/** Размер контейнера по его заголовку (для вычисления хэша установленной модели); 0 — заголовок неверен. */
NOCTIS_API size_t nww_container_total_size(const uint8_t* blob, size_t avail);

/** Скользящее окно вероятностей детектора: срабатывание, если сумма в окне > порог × размер окна
 *  (та же логика, что у моделей microWakeWord в ESPHome). Чистая Си-логика — тестируется на ПК. */
#define NWW_WINDOW_MAX 20
typedef struct {
    float buf[NWW_WINDOW_MAX];
    int size, pos, ignore;
    float cutoff;
} nww_window_t;

NOCTIS_API void nww_window_init(nww_window_t* w, int size, float cutoff);
/** Очищает окно; следующие `ignore` оценок не приводят к срабатыванию (стык потока после паузы). */
NOCTIS_API void nww_window_reset(nww_window_t* w, int ignore);
/** Добавляет оценку; true — условие срабатывания выполнено. */
NOCTIS_API bool nww_window_push(nww_window_t* w, float probability);

/* ============ Смена сети Wi-Fi и связи без сброса платы ============ */

/** Сообщение wifi_result (JSON): ok=true — плата в новой сети и снова на связи; иначе вернулась в прежнюю. */
NOCTIS_API int noctis_build_wifi_result(char* out, size_t cap, const char* request_id, bool ok, const char* error);

typedef enum { NOCTIS_WIFI_NONE = 0, NOCTIS_WIFI_COMMIT, NOCTIS_WIFI_ROLLBACK } noctis_wifi_decision_t;

/** Безопасный переход в другую сеть: если за timeout_ms не удалось подключиться И связаться с сервером — откат. */
typedef struct {
    bool trying;
    int64_t started_ms;
    int64_t timeout_ms;
    bool report_pending;  // итог ещё не отправлен серверу
    bool report_ok;
    char request_id[NOCTIS_REQID_MAX];
} noctis_wifi_switch_t;

NOCTIS_API void noctis_wifi_switch_begin(noctis_wifi_switch_t* sw, const char* request_id, int64_t now_ms,
                                         int64_t timeout_ms);

/** Вызывать периодически. server_ok — есть рабочий сеанс с сервером. COMMIT — оставить новую сеть, ROLLBACK — вернуть прежнюю. */
NOCTIS_API noctis_wifi_decision_t noctis_wifi_switch_poll(noctis_wifi_switch_t* sw, int64_t now_ms, bool server_ok);

/** true один раз, когда итог можно отправить серверу (server_ok); заполняет ok и request_id. */
NOCTIS_API bool noctis_wifi_switch_take_report(noctis_wifi_switch_t* sw, bool server_ok, bool* ok, char request_id[NOCTIS_REQID_MAX]);

/** Слежение за потерей связи с сервером: когда плата без сервера дольше threshold_ms, один раз за период возвращает true
 *  (пора открыть окно Bluetooth-настройки). После восстановления связи период сбрасывается. */
typedef struct {
    bool offline;
    bool fired;
    int64_t since_ms;
} noctis_offline_t;

NOCTIS_API void noctis_offline_init(noctis_offline_t* t);
NOCTIS_API bool noctis_offline_poll(noctis_offline_t* t, int64_t now_ms, bool server_ok, int64_t threshold_ms);

#ifdef __cplusplus
}
#endif
