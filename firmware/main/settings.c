#include "settings.h"

#include <string.h>

#include "esp_log.h"
#include "esp_random.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"

static const char* TAG = "settings";
#define NS "noctis"

static SemaphoreHandle_t s_lock;
static noctis_settings_t s_cur;
static noctis_link_t s_link;
static bool s_has_link = false;
static char s_pop[16];

static void load_str(nvs_handle_t h, const char* key, char* dst, size_t cap) {
    size_t len = cap;
    char tmp[NOCTIS_URL_MAX];
    if (cap > sizeof(tmp)) return;
    len = sizeof(tmp);
    if (nvs_get_str(h, key, tmp, &len) == ESP_OK) noctis_copy(dst, cap, tmp);
}

static void save_settings_locked(nvs_handle_t h) {
    nvs_set_str(h, "wake", s_cur.wake_word);
    nvs_set_str(h, "voice", s_cur.voice_gender);
    nvs_set_str(h, "char", s_cur.character_id);
    nvs_set_str(h, "lang", s_cur.language);
    nvs_set_str(h, "tz", s_cur.timezone);
    nvs_set_i32(h, "vol", s_cur.volume);
    nvs_set_i32(h, "rev", s_cur.revision);
    nvs_commit(h);
}

esp_err_t settings_init(void) {
    s_lock = xSemaphoreCreateMutex();
    noctis_settings_defaults(&s_cur);

    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) return err;

    load_str(h, "wake", s_cur.wake_word, sizeof(s_cur.wake_word));
    load_str(h, "voice", s_cur.voice_gender, sizeof(s_cur.voice_gender));
    load_str(h, "char", s_cur.character_id, sizeof(s_cur.character_id));
    load_str(h, "lang", s_cur.language, sizeof(s_cur.language));
    load_str(h, "tz", s_cur.timezone, sizeof(s_cur.timezone));
    int32_t v;
    if (nvs_get_i32(h, "vol", &v) == ESP_OK) s_cur.volume = v;
    if (nvs_get_i32(h, "rev", &v) == ESP_OK) s_cur.revision = v;
    if (noctis_settings_sanitize(&s_cur) > 0) ESP_LOGW(TAG, "В NVS были некорректные значения — исправлены");

    memset(&s_link, 0, sizeof(s_link));
    load_str(h, "srv", s_link.server, sizeof(s_link.server));
    load_str(h, "did", s_link.device_id, sizeof(s_link.device_id));
    load_str(h, "tok", s_link.token, sizeof(s_link.token));
    uint8_t tls = 0;
    nvs_get_u8(h, "tls", &tls);
    s_link.tls = tls != 0;
    s_has_link = s_link.server[0] && s_link.device_id[0] && s_link.token[0];

    load_str(h, "pop", s_pop, sizeof(s_pop));
    if (strlen(s_pop) != 8) {  // первый запуск: 8 случайных цифр
        for (int i = 0; i < 8; i++) s_pop[i] = '0' + (esp_random() % 10);
        s_pop[8] = '\0';
        nvs_set_str(h, "pop", s_pop);
        nvs_commit(h);
    }
    nvs_close(h);
    ESP_LOGI(TAG, "Настройки: слово=%s голос=%s характер=%s язык=%s громкость=%d rev=%d, сервер: %s", s_cur.wake_word,
             s_cur.voice_gender, s_cur.character_id, s_cur.language, s_cur.volume, s_cur.revision,
             s_has_link ? s_link.server : "не задан");
    return ESP_OK;
}

void settings_get(noctis_settings_t* out) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    *out = s_cur;
    xSemaphoreGive(s_lock);
}

bool settings_apply(const noctis_settings_t* incoming, bool* wake_word_changed) {
    noctis_settings_t n = *incoming;
    noctis_settings_sanitize(&n);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool changed = memcmp(&n, &s_cur, sizeof(n)) != 0;
    if (wake_word_changed) *wake_word_changed = strcmp(n.wake_word, s_cur.wake_word) != 0;
    if (changed) {
        s_cur = n;
        nvs_handle_t h;
        if (nvs_open(NS, NVS_READWRITE, &h) == ESP_OK) {
            save_settings_locked(h);
            nvs_close(h);
        }
    }
    xSemaphoreGive(s_lock);
    return changed;
}

void settings_set_volume(int volume) {
    noctis_settings_t s;
    settings_get(&s);
    s.volume = volume;
    settings_apply(&s, NULL);
}

bool settings_get_link(noctis_link_t* out) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool ok = s_has_link;
    if (ok) *out = s_link;
    xSemaphoreGive(s_lock);
    return ok;
}

esp_err_t settings_set_link(const noctis_link_t* link) {
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) return err;
    nvs_set_str(h, "srv", link->server);
    nvs_set_str(h, "did", link->device_id);
    nvs_set_str(h, "tok", link->token);
    nvs_set_u8(h, "tls", link->tls ? 1 : 0);
    err = nvs_commit(h);
    nvs_close(h);
    if (err == ESP_OK) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        s_link = *link;
        s_has_link = true;
        xSemaphoreGive(s_lock);
    }
    return err;
}

bool settings_get_reconfigure(void) {
    nvs_handle_t h;
    uint8_t v = 0;
    if (nvs_open(NS, NVS_READONLY, &h) == ESP_OK) {
        nvs_get_u8(h, "reconf", &v);
        nvs_close(h);
    }
    return v != 0;
}

void settings_set_reconfigure(bool on) {
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_u8(h, "reconf", on ? 1 : 0);
        nvs_commit(h);
        nvs_close(h);
    }
}

void settings_get_pop(char out[16]) { noctis_copy(out, 16, s_pop); }

void settings_factory_reset(void) {
    ESP_LOGW(TAG, "Сброс к заводским настройкам");
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_erase_all(h);
        nvs_commit(h);
        nvs_close(h);
    }
    esp_wifi_restore();  // удаляет сохранённую сеть Wi-Fi
}
