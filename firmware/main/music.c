#include "music.h"

#include <stdio.h>
#include <string.h>

#include "audio_out.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "noctis_audio.h"

#define MUSIC_BUFFER_BYTES (384 * 1024)  // 12 с звука в PSRAM; сервер опережает воспроизведение на 6 с (media/player.py LEAD_SEC)
#define MUSIC_PREBUFFER_BYTES 32000     // 1 с: накопить перед стартом и после опустошения
#define MUSIC_GAIN_PERCENT 50            // музыка тише голоса: нет эхоподавления, иначе слово пробуждения не слышно

static const char* TAG = "music";
static noctis_player_t s_player;
static SemaphoreHandle_t s_lock;
static char s_label[2 * PLAYER_TITLE_MAX + 8] = "";

static void music_task(void* arg) {
    static int16_t pcm[512];
    for (;;) {
        // Музыка играет только в режиме ожидания: пока идёт диалог, данные копятся в буфере (сервер на это время ставит паузу)
        if (!audio_is_idle()) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        xSemaphoreTake(s_lock, portMAX_DELAY);
        const size_t n = noctis_player_pull(&s_player, (uint8_t*)pcm, sizeof(pcm));
        xSemaphoreGive(s_lock);
        if (n == 0) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        noctis_apply_gain(pcm, n / 2, noctis_music_gain_q8(audio_get_volume(), MUSIC_GAIN_PERCENT));
        audio_write_music(pcm, n / 2);
    }
}

void music_init(void) {
    s_lock = xSemaphoreCreateMutex();
    uint8_t* storage = heap_caps_malloc(MUSIC_BUFFER_BYTES, MALLOC_CAP_SPIRAM);
    if (storage == NULL) {
        ESP_LOGE(TAG, "Нет PSRAM под буфер музыки — музыка отключена");
        return;
    }
    noctis_player_init(&s_player, storage, MUSIC_BUFFER_BYTES);
    noctis_player_set_prebuffer(&s_player, MUSIC_PREBUFFER_BYTES);
    xTaskCreatePinnedToCore(music_task, "music", 4096, NULL, 4, NULL, 1);
    ESP_LOGI(TAG, "Плеер готов: буфер %d КБ", MUSIC_BUFFER_BYTES / 1024);
}

void music_handle(const char* json, size_t len) {
    if (s_lock == NULL || s_player.ring.buf == NULL) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const player_msg_t kind = noctis_player_message(&s_player, json, len);
    if (kind == PLAYER_MSG_START) {
        if (s_player.artist[0]) snprintf(s_label, sizeof(s_label), "%s - %s", s_player.artist, s_player.title);
        else snprintf(s_label, sizeof(s_label), "%s", s_player.title);
        ESP_LOGI(TAG, "Трек: %s (%s)", s_label, s_player.source);
    }
    if (kind == PLAYER_MSG_END || kind == PLAYER_MSG_STOP) {
        ESP_LOGI(TAG, "Конец потока (потеряно %u Б, опустошений буфера %u)", (unsigned)s_player.overruns,
                 (unsigned)s_player.underruns);
    }
    xSemaphoreGive(s_lock);
}

void music_stop_local(void) {
    if (s_lock == NULL || s_player.ring.buf == NULL) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    noctis_player_stop(&s_player);
    xSemaphoreGive(s_lock);
}

bool music_active(void) { return s_player.ring.buf != NULL && noctis_player_active(&s_player); }

const char* music_title(void) { return s_label; }
