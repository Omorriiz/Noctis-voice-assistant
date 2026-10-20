/**
 * Noctis — прошивка ESP32-S3 (ESP-IDF + esp-sr). Имя ассистента (слово пробуждения): Нильс (Nils).
 *
 * Функции:
 * - Постоянное прослушивание микрофона INMP441 и поиск ключевого слова «Привет ESP»
 *   (esp-sr WakeNet10, модель wn10_ru_privetesp) — прямо на ESP32, без сервера
 * - После слова: сигнал, запись команды до паузы в речи (VAD esp-sr)
 * - Отправка PCM16 16kHz mono по WebSocket на сервер (тот же протокол, что и раньше)
 * - Воспроизведение ответа через MAX98357A
 * - Состояния: LISTENING → RECORDING → SENDING → PROCESSING → PLAYING → LISTENING
 *
 * Протокол WebSocket не изменился:
 *   → start_recording, audio_chunk {index, data(base64)}, stop_recording
 *   ← hello, text_response, audio_response_start, audio_response_chunk {data}, audio_response_end
 *
 * Динамик настроен как в рабочем тесте MAX98357A: 16 бит, 16 кГц, пины I2S_AMP_* из config.h.
 * Сеть и сервер не зашиты в код: Wi-Fi и ссылка на сервер приходят по BLE-сопряжению и хранятся в NVS
 * (provisioning.c, settings.c). Персонализация и команды (reboot / OTA) — control.c, ota.c.
 * Пины и тонкая настройка — include/config.h.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "driver/i2s_std.h"
#include "esp_websocket_client.h"
#include "cJSON.h"
#include "mbedtls/base64.h"

#include "esp_afe_sr_models.h"
#include "esp_afe_config.h"
#include "esp_wn_iface.h"
#include "esp_vad.h"
#include "model_path.h"

#include "audio_out.h"
#include "clock.h"
#include "config.h"
#include "control.h"
#include "music.h"
#include "noctis_sched.h"
#include "schedule.h"
#include "display.h"
#include "nww.h"
#include "ota.h"
#include "provisioning.h"
#include "settings.h"
#include "driver/gpio.h"
#include "netwatch.h"
#include "freertos/semphr.h"
#include "freertos/stream_buffer.h"
#include "freertos/timers.h"

// ============ Значения по умолчанию (можно переопределить в config.h) ============
#ifndef SAMPLE_RATE
#define SAMPLE_RATE 16000
#endif
#if SAMPLE_RATE != 16000
#error "esp-sr works only at 16 kHz: set SAMPLE_RATE 16000 in config.h"
#endif
#ifndef MAX_RECORD_SECONDS
#define MAX_RECORD_SECONDS 10
#endif
#ifndef MAX_SAMPLES
#define MAX_SAMPLES (SAMPLE_RATE * MAX_RECORD_SECONDS)
#endif
#ifndef AUDIO_CHUNK_BYTES
#define AUDIO_CHUNK_BYTES 3200  // 100 мс PCM16 @ 16 kHz
#endif
#ifndef MIC_SHIFT
#define MIC_SHIFT 14  // INMP441: 24 бита в 32-битном слоте. 16 = как есть, 14 = +12 dB
#endif
#ifndef MIC_USE_RIGHT_CHANNEL
#define MIC_USE_RIGHT_CHANNEL 0  // 0: L/R пин INMP441 на GND, 1: на 3.3 V
#endif

#if !defined(I2S_MIC_BCK) || !defined(I2S_MIC_WS) || !defined(I2S_MIC_DIN)
#error "Define I2S_MIC_BCK, I2S_MIC_WS and I2S_MIC_DIN (INMP441 pins) in config.h"
#endif
#if I2S_MIC_BCK < 0 || I2S_MIC_WS < 0 || I2S_MIC_DIN < 0
#error "Set the real INMP441 pins in config.h"
#endif

// ============ Пины динамика (рабочий тест MAX98357A: 5/6/7) ============
#ifndef I2S_AMP_BCLK
#define I2S_AMP_BCLK 5
#endif
#ifndef I2S_AMP_LRC
#define I2S_AMP_LRC 6
#endif
#ifndef I2S_AMP_DIN
#define I2S_AMP_DIN 7
#endif
#define SPK_BCLK_PIN I2S_AMP_BCLK
#define SPK_LRC_PIN  I2S_AMP_LRC
#define SPK_DIN_PIN  I2S_AMP_DIN

#if I2S_MIC_BCK == SPK_BCLK_PIN || I2S_MIC_BCK == SPK_LRC_PIN || I2S_MIC_BCK == SPK_DIN_PIN || \
    I2S_MIC_WS == SPK_BCLK_PIN || I2S_MIC_WS == SPK_LRC_PIN || I2S_MIC_WS == SPK_DIN_PIN ||    \
    I2S_MIC_DIN == SPK_BCLK_PIN || I2S_MIC_DIN == SPK_LRC_PIN || I2S_MIC_DIN == SPK_DIN_PIN
#error "INMP441 pins conflict with MAX98357A pins (I2S_AMP_*)"
#endif

// ============ Распознавание речи ============
// Слово пробуждения выбирается при загрузке по настройке wake_word (settings.c):
//   nils / custom:*  -> microWakeWord (модель из раздела wakeword, nww_*)
//   остальные пресеты -> esp-sr WakeNet (модель из раздела model)
#define WAKE_WORD_FALLBACK   "privetesp"  // wn10_ru_privetesp — если модели microWakeWord нет
#define VAD_END_SILENCE_MS   700    // пауза, после которой команда считается законченной
#define WAKE_GRACE_MS        (VAD_END_SILENCE_MS + 300)  // хвост самого «Привет ESP» не считается командой
#define NO_SPEECH_TIMEOUT_MS 6000   // после слова ничего не сказали — вернуться к ожиданию

// ============ Тайминги ============
#define PROCESSING_TIMEOUT_MS 45000
#define PLAYING_TIMEOUT_MS    30000

static const char* TAG = "noctis";

// ============ Состояния ============
typedef enum {
    ST_LISTENING,   // ждём «Привет ESP»
    ST_RECORDING,   // пишем команду
    ST_SENDING,     // отправляем запись на сервер
    ST_PROCESSING,  // ждём ответ сервера
    ST_PLAYING      // играем ответ
} device_state_t;

static volatile device_state_t s_state = ST_LISTENING;
static volatile uint32_t s_state_since_ms = 0;
static volatile uint32_t s_last_audio_ms = 0;
static noctis_engine_t s_engine = NOCTIS_ENGINE_ESP_SR;  // кто слушает слово пробуждения
static volatile bool s_mww_hit = false;                  // microWakeWord сработал (ставит feed_task)
static char s_installed_sha[65] = "";                    // SHA-256 модели в разделе wakeword
static char s_wake_label[64] = "Привет ESP";            // как называть слово в логах
static volatile bool s_resume_request = false;  // вернуться к прослушиванию (выполняет detect_task)

// ============ Объекты ============
static i2s_chan_handle_t s_mic = NULL;
static i2s_chan_handle_t s_spk = NULL;
static SemaphoreHandle_t s_spk_lock = NULL;  // динамик делят голос, звонок и музыка
static esp_websocket_client_handle_t s_ws = NULL;
static const esp_afe_sr_iface_t* s_afe = NULL;
static esp_afe_sr_data_t* s_afe_data = NULL;
static TaskHandle_t s_sender_task = NULL;
static bool s_ws_started = false;
static int s_ws_failures = 0;  // подряд неудачных подключений (для нарастающей паузы)

// ============ Буфер записи (PSRAM) ============
static int16_t* s_rec_buf = NULL;
static volatile size_t s_rec_samples = 0;
static bool s_speech_started = false;

// ============ Приём WebSocket ============
static char* s_rx_buf = NULL;
static size_t s_rx_cap = 0;
static size_t s_incoming_chunks = 0;

static inline uint32_t now_ms(void) { return (uint32_t)(esp_timer_get_time() / 1000); }

static void set_state(device_state_t state) {
    s_state = state;
    s_state_since_ms = now_ms();
}

bool audio_is_idle(void) { return s_state == ST_LISTENING; }

// ============================================================
// ДИНАМИК (MAX98357A)
// ============================================================
static void speaker_init(void) {
    s_spk_lock = xSemaphoreCreateMutex();
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;  // тишина, когда нечего играть
    chan_cfg.dma_desc_num = 6;
    chan_cfg.dma_frame_num = 320;  // 6 × 320 кадров = 120 мс в DMA (было 90 мс): запас на задержку задачи воспроизведения
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &s_spk, NULL));

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = SPK_BCLK_PIN,
            .ws = SPK_LRC_PIN,
            .dout = SPK_DIN_PIN,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
        },
    };
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;  // моно-сэмпл в оба канала: MAX98357A слышит его в любом режиме
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_spk, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(s_spk));
    ESP_LOGI(TAG, "Динамик MAX98357A: BCLK=%d LRC=%d DIN=%d", SPK_BCLK_PIN, SPK_LRC_PIN, SPK_DIN_PIN);
}

// Громкость 0..100 -> усиление в формате Q8; квадратичная кривая ближе к восприятию громкости
static volatile int s_gain_q8 = 256 * 70 * 70 / 10000;
static volatile int s_volume = 70;

void audio_set_volume(int volume) {
    if (volume < 0) volume = 0;
    if (volume > 100) volume = 100;
    s_volume = volume;
    s_gain_q8 = 256 * volume * volume / 10000;
}

int audio_get_volume(void) { return s_volume; }

static void speaker_write_gain(const int16_t* samples, size_t count, int gain) {
    int16_t scaled[256];
    xSemaphoreTake(s_spk_lock, portMAX_DELAY);
    while (count > 0) {
        const size_t n = count < 256 ? count : 256;
        for (size_t i = 0; i < n; i++) scaled[i] = (int16_t)((samples[i] * gain) >> 8);
        size_t written = 0;
        const size_t bytes = n * sizeof(int16_t);
        esp_err_t err = i2s_channel_write(s_spk, scaled, bytes, &written, 1000);
        if (err != ESP_OK || written != bytes) {
            ESP_LOGW(TAG, "i2s write: %s (%u/%u)", esp_err_to_name(err), (unsigned)written, (unsigned)bytes);
        }
        samples += n;
        count -= n;
    }
    xSemaphoreGive(s_spk_lock);
}

static void speaker_write(const int16_t* samples, size_t count) { speaker_write_gain(samples, count, s_gain_q8); }

// Музыка: усиление уже применено плеером (music.c), поэтому единичное
void audio_write_music(const int16_t* samples, size_t count) { speaker_write_gain(samples, count, 256); }

// Тон для звонка будильника/таймера. alarm: громкость не ниже 50 %, даже если голос выключен (0)
void audio_play_tone(int freq_hz, int ms, bool alarm) {
    static int16_t buf[256];
    const int min_gain = 256 * 50 * 50 / 10000;
    const int gain = (alarm && s_gain_q8 < min_gain) ? min_gain : s_gain_q8;
    const float step = 2.0f * 3.14159265f * freq_hz / SAMPLE_RATE;
    float phase = 0.0f;
    const int total = SAMPLE_RATE * ms / 1000;
    for (int offset = 0; offset < total; offset += 256) {
        const int n = (total - offset) < 256 ? (total - offset) : 256;
        for (int i = 0; i < n; i++) {
            buf[i] = (int16_t)(8000.0f * sinf(phase));
            phase += step;
        }
        speaker_write_gain(buf, n, gain);
    }
}

static void play_beep(int freq_hz, int ms) {
    static int16_t buf[256];
    const float step = 2.0f * 3.14159265f * freq_hz / SAMPLE_RATE;
    float phase = 0.0f;
    const int total = SAMPLE_RATE * ms / 1000;
    for (int offset = 0; offset < total; offset += 256) {
        const int n = (total - offset) < 256 ? (total - offset) : 256;
        for (int i = 0; i < n; i++) {
            buf[i] = (int16_t)(6000.0f * sinf(phase));
            phase += step;
        }
        speaker_write(buf, n);
    }
}

// ============================================================
// ВОСПРОИЗВЕДЕНИЕ ОТВЕТА (моно PCM16)
// ============================================================
// Приём (задача WebSocket) и воспроизведение разделены буфером в PSRAM: рывки Wi-Fi и работа сетевых задач
// не слышны, пока в буфере есть запас. Играет отдельная задача: ждёт предзапас, при опустошении набирает его заново.
#define PB_RING_BYTES    (384 * 1024)   // 12 с PCM16 16 кГц
#define PB_PREROLL_BYTES 24000          // 0,75 с — накопить перед стартом и после опустошения
#define PB_SEND_WAIT_MS  3000           // буфер полон: ждём освобождения (сервер опережает не более чем на TTS_LEAD_SEC)
#define PCM_TMP_BYTES    8192

static StreamBufferHandle_t s_pb = NULL;
static StaticStreamBuffer_t s_pb_ctrl;
static uint8_t* s_pb_storage = NULL;
static uint8_t* s_pcm_tmp = NULL;             // декодированный чанк (PSRAM)
static uint8_t s_play_carry = 0;              // нечётный байт, разрезанный между чанками
static bool s_play_has_carry = false;
static volatile bool s_pb_active = false;     // идёт ответ (от audio_response_start до конца воспроизведения)
static volatile bool s_pb_end = false;        // сервер закончил передачу
static uint32_t s_pb_underruns = 0;           // опустошений буфера посреди ответа
static uint32_t s_pb_dropped = 0;             // байт не поместилось (буфер полон дольше PB_SEND_WAIT_MS)
static uint32_t s_pb_last_rx_ms = 0, s_pb_max_gap_ms = 0, s_pb_gaps = 0;   // паузы приёма: показатель связи
static size_t s_pb_peak = 0;                  // наибольшее заполнение буфера

static void pb_clear(void) {
    uint8_t scratch[256];
    while (s_pb && xStreamBufferReceive(s_pb, scratch, sizeof(scratch), 0) > 0) {}
    s_play_has_carry = false;
}

static void play_reset(void) {
    s_pb_active = false;
    s_pb_end = false;
    pb_clear();
}

static void play_begin(void) {
    pb_clear();
    s_pb_end = false;
    s_pb_underruns = s_pb_dropped = s_pb_gaps = s_pb_max_gap_ms = 0;
    s_pb_peak = 0;
    s_pb_last_rx_ms = now_ms();
    s_pb_active = true;
}

// Из задачи WebSocket: кладём PCM в буфер (только целые 16-битные отсчёты)
static void play_pcm16(const uint8_t* data, size_t len) {
    if (s_pb == NULL || len == 0) return;
    const uint32_t t = now_ms();
    const uint32_t gap = t - s_pb_last_rx_ms;
    s_pb_last_rx_ms = t;
    if (gap > 200) {
        s_pb_gaps++;
        if (gap > s_pb_max_gap_ms) s_pb_max_gap_ms = gap;
    }
    uint8_t joined[2];
    if (s_play_has_carry) {
        joined[0] = s_play_carry;
        joined[1] = data[0];
        xStreamBufferSend(s_pb, joined, 2, pdMS_TO_TICKS(PB_SEND_WAIT_MS));
        s_play_has_carry = false;
        data++;
        len--;
    }
    if (len & 1) {
        s_play_carry = data[len - 1];
        s_play_has_carry = true;
        len--;
    }
    while (len > 0) {
        const size_t put = xStreamBufferSend(s_pb, data, len, pdMS_TO_TICKS(PB_SEND_WAIT_MS));
        if (put == 0) {
            s_pb_dropped += (uint32_t)len;
            break;
        }
        data += put;
        len -= put;
    }
    const size_t fill = xStreamBufferBytesAvailable(s_pb);
    if (fill > s_pb_peak) s_pb_peak = fill;
}

static void playback_task(void* arg) {
    static uint8_t buf[1024];
    static int16_t silence[256];
    uint8_t carry = 0;
    bool has_carry = false;
    bool playing = false;
    for (;;) {
        if (!s_pb_active) {
            vTaskDelay(pdMS_TO_TICKS(10));
            playing = false;
            has_carry = false;
            continue;
        }
        const size_t avail = xStreamBufferBytesAvailable(s_pb);
        if (!playing) {
            if (avail >= PB_PREROLL_BYTES || (s_pb_end && avail > 0)) {
                playing = true;
            } else if (s_pb_end && avail == 0) {
                // ответ закончился, не дав звука
                s_pb_active = false;
                s_resume_request = true;
                continue;
            } else {
                vTaskDelay(pdMS_TO_TICKS(10));
                continue;
            }
        }
        size_t n = 0;
        if (has_carry) {
            buf[0] = carry;
            n = xStreamBufferReceive(s_pb, buf + 1, sizeof(buf) - 1, pdMS_TO_TICKS(40)) + 1;
            has_carry = false;
        } else {
            n = xStreamBufferReceive(s_pb, buf, sizeof(buf), pdMS_TO_TICKS(40));
        }
        if (n & 1) {
            carry = buf[n - 1];
            has_carry = true;
            n--;
        }
        if (n >= 2) {
            speaker_write((const int16_t*)buf, n / 2);
            s_last_audio_ms = now_ms();
            continue;
        }
        // данных нет
        if (s_pb_end && xStreamBufferBytesAvailable(s_pb) == 0) {
            // хвост: тишина, чтобы последний звук доиграл из DMA и не обрезался
            for (int i = 0; i < 8; i++) speaker_write(silence, 256);
            ESP_LOGI(TAG, "Воспроизведение завершено: опустошений буфера %u, пауз приёма >200 мс %u (макс %u мс), потеряно %u Б, "
                          "наибольшее заполнение %u КБ",
                     (unsigned)s_pb_underruns, (unsigned)s_pb_gaps, (unsigned)s_pb_max_gap_ms, (unsigned)s_pb_dropped,
                     (unsigned)(s_pb_peak / 1024));
            s_pb_active = false;
            playing = false;
            s_resume_request = true;
        } else if (!s_pb_end) {
            s_pb_underruns++;
            playing = false;   // набрать предзапас заново, чтобы не дробить звук
        }
    }
}

// ============================================================
// МИКРОФОН (INMP441)
// ============================================================
// Сколько раз очередь DMA микрофона переполнилась: аудио потеряно (задача чтения не успела) — слово пробуждения разрывается
static volatile uint32_t s_mic_overflows = 0;

static bool IRAM_ATTR mic_overflow_cb(i2s_chan_handle_t handle, i2s_event_data_t* event, void* user) {
    s_mic_overflows++;
    return false;
}

static void mic_init(void) {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 8;
    chan_cfg.dma_frame_num = 256;  // 8 × 256 кадров = 128 мс запаса (было 90 мс): рывки задач Wi-Fi не рвут слово
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, NULL, &s_mic));
    const i2s_event_callbacks_t cbs = {.on_recv_q_ovf = mic_overflow_cb};
    ESP_ERROR_CHECK(i2s_channel_register_event_callback(s_mic, &cbs, NULL));

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_MIC_BCK,
            .ws = I2S_MIC_WS,
            .dout = I2S_GPIO_UNUSED,
            .din = I2S_MIC_DIN,
            .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
        },
    };
    std_cfg.slot_cfg.slot_mask = MIC_USE_RIGHT_CHANNEL ? I2S_STD_SLOT_RIGHT : I2S_STD_SLOT_LEFT;
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_mic, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(s_mic));
    ESP_LOGI(TAG, "Микрофон INMP441: BCK=%d WS=%d DIN=%d", I2S_MIC_BCK, I2S_MIC_WS, I2S_MIC_DIN);
}

// ============================================================
// ESP-SR: WakeNet «Привет ESP» + VAD
// ============================================================
static void afe_init(void) {
    srmodel_list_t* models = esp_srmodel_init("model");
    if (models == NULL) {
        ESP_LOGE(TAG, "Модели esp-sr не найдены в разделе \"model\" (прошейте через idf.py flash)");
        abort();
    }

    noctis_settings_t st;
    settings_get(&st);
    const char* wn_filter = NULL;
    s_engine = noctis_wake_engine(st.wake_word, &wn_filter);

    if (s_engine == NOCTIS_ENGINE_MWW) {
        nww_container_t container;
        if (nww_partition_load(&container, s_installed_sha) && nww_start(&container)) {
            snprintf(s_wake_label, sizeof(s_wake_label), "%s", container.manifest.label);
        } else {
            // Модели нет (или она повреждена): работаем на «Привет ESP», пока сервер не пришлёт модель
            ESP_LOGW(TAG, "Модель слова «%s» недоступна — временно «Привет ESP»", st.wake_word);
            display_error("NO WAKE MODEL", "using Privet ESP");
            s_engine = NOCTIS_ENGINE_ESP_SR;
            wn_filter = WAKE_WORD_FALLBACK;
        }
    }

    char* wn_name = NULL;
    if (s_engine == NOCTIS_ENGINE_ESP_SR) {
        wn_name = esp_srmodel_filter(models, "wn", wn_filter);
        if (wn_name == NULL) {
            ESP_LOGW(TAG, "Модель esp-sr «%s» не найдена в разделе model — беру «Привет ESP»", wn_filter);
            wn_name = esp_srmodel_filter(models, "wn", WAKE_WORD_FALLBACK);
        }
        if (wn_name != NULL && strncmp(wn_name, "wn9", 3) == 0) {
            // Модели семейства WakeNet9 (jarvis, computer) на плате роняют AFE: assert в model_detect_mfcc и цикл перезагрузок.
            // Не запускаем их, пока причина не найдена: работаем на «Привет ESP» и говорим об этом на экране.
            ESP_LOGW(TAG, "Модель «%s» (WakeNet9) вызывает сбой AFE — временно «Привет ESP»", wn_name);
            display_error("WORD UNSUPPORTED", "using Privet ESP");
            wn_name = esp_srmodel_filter(models, "wn", WAKE_WORD_FALLBACK);
        }
        if (wn_name == NULL) {
            ESP_LOGE(TAG, "Модель wn10_ru_privetesp не найдена: включите CONFIG_SR_WN_WN10_RU_PRIVETESP");
            abort();
        }
        snprintf(s_wake_label, sizeof(s_wake_label), "%s", wn_name);
    }

    afe_config_t* cfg = afe_config_init("M", models, AFE_TYPE_SR, AFE_MODE_LOW_COST);
    cfg->memory_alloc_mode = AFE_MEMORY_ALLOC_MORE_PSRAM;  // внутренней памяти и так мало (нужна Wi-Fi)
    cfg->wakenet_init = (s_engine == NOCTIS_ENGINE_ESP_SR);  // у microWakeWord свой детектор, AFE даёт только VAD
    if (wn_name) cfg->wakenet_model_name = wn_name;
    cfg->wakenet_mode = DET_MODE_90;
    cfg->aec_init = false;  // нет опорного канала динамика
    cfg->se_init = false;   // один микрофон
    cfg->vad_init = true;
    cfg->vad_min_noise_ms = VAD_END_SILENCE_MS;

    s_afe = esp_afe_handle_from_config(cfg);
    s_afe_data = s_afe->create_from_config(cfg);
    afe_config_free(cfg);
    if (s_afe_data == NULL) {
        ESP_LOGE(TAG, "Не удалось создать AFE");
        abort();
    }
    ESP_LOGI(TAG, "Слово пробуждения: «%s» (движок: %s)", s_wake_label,
             s_engine == NOCTIS_ENGINE_MWW ? "microWakeWord" : "esp-sr WakeNet");
}

const char* audio_installed_wake_sha(void) { return s_installed_sha; }

// Микрофон → AFE
static void feed_task(void* arg) {
    const int chunk = s_afe->get_feed_chunksize(s_afe_data);
    const int nch = s_afe->get_feed_channel_num(s_afe_data);
    int32_t* raw = heap_caps_malloc(chunk * sizeof(int32_t), MALLOC_CAP_INTERNAL);
    int16_t* feed = heap_caps_calloc(chunk * nch, sizeof(int16_t), MALLOC_CAP_INTERNAL);
    int16_t* mono = heap_caps_calloc(chunk, sizeof(int16_t), MALLOC_CAP_INTERNAL);
    assert(raw && feed && mono);
    int diag_chunks = 0, diag_peak = 0, diag_clips = 0, diag_secs = 0;
    float diag_pmax = 0.0f;
    int64_t work_sum_us = 0, work_max_us = 0, nww_sum_us = 0, nww_max_us = 0;   // загрузка: кадр звука длится 32 мс
    int work_n = 0, nww_n = 0;
    uint32_t ovf_prev = 0;
    float diag_dc = 0.0f;                        // сумма сырых отсчётов за секунду: постоянная составляющая INMP441
    float dc_px = 0.0f, dc_py = 0.0f;            // состояние фильтра, убирающего постоянную составляющую

    for (;;) {
        size_t got = 0;
        if (i2s_channel_read(s_mic, raw, chunk * sizeof(int32_t), &got, portMAX_DELAY) != ESP_OK) continue;
        const int n = (int)(got / sizeof(int32_t));
        const int64_t work_t0 = esp_timer_get_time();
        for (int i = 0; i < chunk; i++) {
            const float x = i < n ? (float)(raw[i] >> MIC_SHIFT) : 0.0f;
            // INMP441 выдаёт постоянную составляющую (в журнале ≈ 7500 из 32767 в тишине): она съедает запас до клиппинга
            // при усилении +12 дБ и искажает речь. ФВЧ первого порядка (≈ 40 Гц): y[n] = x[n] − x[n−1] + 0,995·y[n−1]
            const float y = x - dc_px + 0.995f * dc_py;
            dc_px = x;
            dc_py = y;
            diag_dc += x;
            int32_t s = (int32_t)y;
            if (s > 32767) { s = 32767; diag_clips++; }
            if (s < -32768) { s = -32768; diag_clips++; }
            feed[i * nch] = (int16_t)s;
            mono[i] = (int16_t)s;
            const int a = s < 0 ? -(int)s : (int)s;
            if (a > diag_peak) diag_peak = a;
        }
        // microWakeWord слушает только в режиме ожидания (во время записи/ответа не нужен)
        if (s_engine == NOCTIS_ENGINE_MWW && s_state == ST_LISTENING) {
            const int64_t nt0 = esp_timer_get_time();
            if (nww_feed(mono, chunk)) s_mww_hit = true;
            const int64_t nd = esp_timer_get_time() - nt0;
            nww_sum_us += nd;
            nww_n++;
            if (nd > nww_max_us) nww_max_us = nd;
            const float p = nww_last_probability();
            if (p > diag_pmax) diag_pmax = p;
        }
        // Раз в секунду, пока слышен звук или слово почти распознано: по этой строке подбирают порог и усиление микрофона
        if (++diag_chunks >= 31) {
            diag_secs++;   // на 5-й секунде — строка в любом случае (уровень шума и постоянная составляющая в тишине)
            if (s_state == ST_LISTENING && (diag_pmax > 0.2f || diag_peak > 6000 || diag_secs == 5))
                ESP_LOGI(TAG, "[слух] вероятность слова %.2f, пик %d из 32767, клиппинг %d, постоянная составляющая %d", diag_pmax,
                         diag_peak, diag_clips, (int)(diag_dc / (31.0f * chunk)));
            diag_chunks = 0;
            diag_pmax = 0.0f;
            diag_peak = 0;
            diag_clips = 0;
            diag_dc = 0.0f;
            if (diag_secs % 20 == 0 && work_n > 0) {
                ESP_LOGI(TAG, "[нагрузка] кадр 32 мс: обработка средн. %d мкс, макс. %d мкс; слово: средн. %d мкс, макс. %d мкс; "
                              "потерь аудио микрофона за 20 с: %u",
                         (int)(work_sum_us / work_n), (int)work_max_us, nww_n ? (int)(nww_sum_us / nww_n) : 0, (int)nww_max_us,
                         (unsigned)(s_mic_overflows - ovf_prev));
                ovf_prev = s_mic_overflows;
                work_sum_us = work_max_us = nww_sum_us = nww_max_us = 0;
                work_n = nww_n = 0;
            }
        }
        s_afe->feed(s_afe_data, feed);
        const int64_t wd = esp_timer_get_time() - work_t0;
        work_sum_us += wd;
        work_n++;
        if (wd > work_max_us) work_max_us = wd;
    }
}

// Только из detect_task: AFE не трогаем из нескольких задач одновременно
static void resume_listening(void) {
    play_reset();
    s_afe->reset_buffer(s_afe_data);
    if (s_engine == NOCTIS_ENGINE_ESP_SR) {
        s_afe->enable_wakenet(s_afe_data);
    } else {
        nww_reset();
        s_mww_hit = false;
    }
    set_state(ST_LISTENING);
    ESP_LOGI(TAG, "Слушаю. Скажите «%s»", s_wake_label);
}

static void ws_reconnect_now(void);

static void start_command_recording(void) {
    if (s_ws == NULL || !esp_websocket_client_is_connected(s_ws)) {
        ESP_LOGW(TAG, "Ключевое слово услышано, но сервер не подключён");
        // Без сброса флаг «слово услышано» у microWakeWord залипает и срабатывает на каждом кадре (≈33 раза в секунду)
        if (s_engine == NOCTIS_ENGINE_MWW) nww_reset();
        // Слово услышано — значит, человек ждёт ответа: сразу пробуем подключиться (не чаще раза в 3 с) и даём понять, что связи нет
        static uint32_t s_last_kick_ms = 0;
        if (now_ms() - s_last_kick_ms > 3000) {
            s_last_kick_ms = now_ms();
            ws_reconnect_now();
            display_status("NOCTIS", "no server", "reconnecting...");
            play_beep(330, 120);
            play_beep(250, 160);
        }
        return;
    }
    if (s_engine == NOCTIS_ENGINE_ESP_SR) s_afe->disable_wakenet(s_afe_data);
    s_mww_hit = false;
    s_rec_samples = 0;
    s_speech_started = false;
    schedule_stop_ring(true);  // слово пробуждения останавливает звонок
    set_state(ST_RECORDING);
    ESP_LOGI(TAG, "«%s»! Говорите команду...", s_wake_label);
    play_beep(880, 120);
}

static void record_frame(const afe_fetch_result_t* r) {
    const uint32_t elapsed = now_ms() - s_state_since_ms;

    size_t n = r->data_size / sizeof(int16_t);
    if (s_rec_samples + n > MAX_SAMPLES) n = MAX_SAMPLES - s_rec_samples;
    memcpy(s_rec_buf + s_rec_samples, r->data, n * sizeof(int16_t));
    s_rec_samples += n;

    if (r->vad_state == VAD_SPEECH && elapsed > WAKE_GRACE_MS) s_speech_started = true;

    if (s_speech_started && r->vad_state == VAD_SILENCE) {
        ESP_LOGI(TAG, "Конец фразы: %.2f сек", s_rec_samples / (float)SAMPLE_RATE);
    } else if (s_rec_samples >= MAX_SAMPLES) {
        ESP_LOGI(TAG, "Максимум времени записи");
    } else if (!s_speech_started && elapsed > NO_SPEECH_TIMEOUT_MS) {
        ESP_LOGI(TAG, "Команды не было — отмена");
        play_beep(440, 120);
        resume_listening();
        return;
    } else {
        return;
    }

    set_state(ST_SENDING);
    xTaskNotifyGive(s_sender_task);
}

// AFE → ключевое слово / запись команды / таймауты
static void detect_task(void* arg) {
    ESP_LOGI(TAG, "Слушаю. Скажите «%s»", s_wake_label);
    for (;;) {
        afe_fetch_result_t* r = s_afe->fetch(s_afe_data);
        if (r == NULL || r->ret_value == ESP_FAIL) continue;

        if (s_resume_request) {
            s_resume_request = false;
            resume_listening();
            continue;
        }

        switch (s_state) {
            case ST_LISTENING:
                if (s_engine == NOCTIS_ENGINE_ESP_SR ? (r->wakeup_state == WAKENET_DETECTED) : s_mww_hit) {
                    s_mww_hit = false;
                    start_command_recording();
                }
                break;
            case ST_RECORDING:
                record_frame(r);
                break;
            case ST_PROCESSING:
                if (now_ms() - s_state_since_ms > PROCESSING_TIMEOUT_MS) {
                    ESP_LOGW(TAG, "Таймаут обработки");
                    resume_listening();
                }
                break;
            case ST_PLAYING:
                if (now_ms() - s_last_audio_ms > PLAYING_TIMEOUT_MS) {
                    ESP_LOGW(TAG, "Таймаут воспроизведения");
                    play_reset();
                    resume_listening();
                }
                break;
            default:
                break;  // ST_SENDING: аудио игнорируется, пока идёт отправка
        }
    }
}

// ============================================================
// ОТПРАВКА ЗАПИСИ
// ============================================================
static bool ws_send(const char* text, int len) {
    if (!esp_websocket_client_is_connected(s_ws)) return false;
    return esp_websocket_client_send_text(s_ws, text, len, pdMS_TO_TICKS(5000)) >= 0;
}

static void sender_task(void* arg) {
    static unsigned char b64[((AUDIO_CHUNK_BYTES + 2) / 3) * 4 + 1];
    static char json[sizeof(b64) + 64];

    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        const uint8_t* raw = (const uint8_t*)s_rec_buf;
        const size_t total = s_rec_samples * sizeof(int16_t);
        ESP_LOGI(TAG, "Отправка %u байт аудио...", (unsigned)total);

        const char* start = "{\"type\":\"start_recording\"}";
        bool ok = ws_send(start, strlen(start));
        int index = 0;
        for (size_t offset = 0; ok && offset < total; offset += AUDIO_CHUNK_BYTES) {
            const size_t size = (total - offset) < AUDIO_CHUNK_BYTES ? (total - offset) : AUDIO_CHUNK_BYTES;
            size_t b64_len = 0;
            if (mbedtls_base64_encode(b64, sizeof(b64), &b64_len, raw + offset, size) != 0) {
                ok = false;
                break;
            }
            const int len = snprintf(json, sizeof(json), "{\"type\":\"audio_chunk\",\"index\":%d,\"data\":\"%s\"}",
                                     index, (const char*)b64);
            ok = ws_send(json, len);
            index++;
        }

        if (!ok) {
            ESP_LOGW(TAG, "Ошибка отправки — возвращаюсь к прослушиванию");
            s_resume_request = true;
            continue;
        }

        set_state(ST_PROCESSING);  // до stop_recording: ответ может прийти сразу
        const char* stop = "{\"type\":\"stop_recording\"}";
        if (!ws_send(stop, strlen(stop))) {
            s_resume_request = true;
            continue;
        }
        ESP_LOGI(TAG, "Отправлено %d чанков, жду ответ", index);
    }
}

// ============================================================
// ПРИЁМ СООБЩЕНИЙ СЕРВЕРА
// ============================================================
static void handle_server_message(const char* text, size_t len) {
    cJSON* root = cJSON_ParseWithLength(text, len);
    if (root == NULL) {
        ESP_LOGW(TAG, "JSON ошибка");
        return;
    }
    const cJSON* type = cJSON_GetObjectItemCaseSensitive(root, "type");
    const char* t = cJSON_IsString(type) ? type->valuestring : "";

    if (strcmp(t, "config") == 0 || strcmp(t, "command") == 0) {
        control_handle(text, len);
    } else if (strcmp(t, "time_sync") == 0) {
        int64_t epoch;
        int32_t offset;
        if (noctis_parse_time_sync(text, len, &epoch, &offset)) clock_apply_server(epoch, offset);
    } else if (strcmp(t, "schedule") == 0) {
        schedule_apply(text, len);
    } else if (strncmp(t, "music_", 6) == 0) {
        music_handle(text, len);
    } else if (strcmp(t, "hello") == 0) {
        const cJSON* msg = cJSON_GetObjectItemCaseSensitive(root, "message");
        ESP_LOGI(TAG, "Сервер: %s", cJSON_IsString(msg) ? msg->valuestring : "");
    } else if (strcmp(t, "text_response") == 0) {
        const cJSON* msg = cJSON_GetObjectItemCaseSensitive(root, "text");
        ESP_LOGI(TAG, "[LLM] %s", cJSON_IsString(msg) ? msg->valuestring : "");
    } else if (strcmp(t, "audio_response_start") == 0) {
        s_incoming_chunks = 0;
        schedule_stop_ring(false);  // голосовое уведомление/ответ: звонок замолкает
        s_last_audio_ms = now_ms();
        play_begin();
        set_state(ST_PLAYING);
        ESP_LOGI(TAG, "Начало воспроизведения...");
    } else if (strcmp(t, "audio_response_chunk") == 0 && s_state == ST_PLAYING) {
        const cJSON* data = cJSON_GetObjectItemCaseSensitive(root, "data");
        if (cJSON_IsString(data) && data->valuestring[0] != '\0') {
            const size_t b64_len = strlen(data->valuestring);
            size_t pcm_len = 0;
            if (s_pcm_tmp != NULL && (b64_len / 4) * 3 + 3 <= PCM_TMP_BYTES &&
                mbedtls_base64_decode(s_pcm_tmp, PCM_TMP_BYTES, &pcm_len, (const unsigned char*)data->valuestring, b64_len) == 0) {
                play_pcm16(s_pcm_tmp, pcm_len);
                s_incoming_chunks++;
                s_last_audio_ms = now_ms();
            } else {
                ESP_LOGW(TAG, "Ошибка base64");
            }
        }
    } else if (strcmp(t, "audio_response_end") == 0) {
        ESP_LOGI(TAG, "Ответ принят (%u чанков), доигрывает из буфера", (unsigned)s_incoming_chunks);
        if (s_pb_active) s_pb_end = true;
        else s_resume_request = true;
    }
    cJSON_Delete(root);
}

static void ws_schedule_reconnect(void);
static void ws_show_idle(void);

bool net_server_connected(void) { return s_ws != NULL && esp_websocket_client_is_connected(s_ws); }

static void ws_event(void* arg, esp_event_base_t base, int32_t id, void* event_data) {
    esp_websocket_event_data_t* d = (esp_websocket_event_data_t*)event_data;
    switch (id) {
        case WEBSOCKET_EVENT_CONNECTED:
            ESP_LOGI(TAG, "WebSocket подключён");
            s_ws_failures = 0;
            control_send_device_info();
            ws_show_idle();
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
        case WEBSOCKET_EVENT_ERROR:
            ESP_LOGW(TAG, "WebSocket отключён");
            if (s_state == ST_PROCESSING || s_state == ST_PLAYING) s_resume_request = true;
            ws_schedule_reconnect();
            break;
        case WEBSOCKET_EVENT_DATA: {
            if (d->op_code != 0x01 && d->op_code != 0x00) break;  // только текст (и его продолжение)
            if (d->payload_len <= 0 || d->data_len <= 0) break;
            // Большие сообщения приходят частями: собираем по payload_offset
            if (d->payload_offset == 0 && s_rx_cap < (size_t)d->payload_len + 1) {
                free(s_rx_buf);
                s_rx_cap = d->payload_len + 1;
                s_rx_buf = heap_caps_malloc(s_rx_cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                if (s_rx_buf == NULL) {
                    s_rx_cap = 0;
                    ESP_LOGE(TAG, "Нет памяти для сообщения %d байт", d->payload_len);
                    break;
                }
            }
            if (s_rx_buf == NULL || (size_t)(d->payload_offset + d->data_len) >= s_rx_cap) break;
            memcpy(s_rx_buf + d->payload_offset, d->data_ptr, d->data_len);
            if (d->payload_offset + d->data_len >= d->payload_len) {
                s_rx_buf[d->payload_len] = '\0';
                handle_server_message(s_rx_buf, d->payload_len);
            }
            break;
        }
        default:
            break;
    }
}

// Повтор подключения с нарастающей паузой (5, 10, 20, 40, 60 с): при неверном токене или
// недоступном сервере устройство не заваливает его запросами.
// Блокирующие stop/start клиента нельзя выполнять в задаче таймеров FreeRTOS (стек 2 КБ — «stack overflow in Tmr Svc»):
// таймер только будит отдельную задачу с нормальным стеком.
static TaskHandle_t s_ws_retry_task = NULL;

static void ws_retry_task(void* arg) {
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (s_ws == NULL || esp_websocket_client_is_connected(s_ws)) continue;
        esp_websocket_client_stop(s_ws);
        esp_websocket_client_start(s_ws);
    }
}

static void ws_reconnect_cb(TimerHandle_t t) {
    if (s_ws_retry_task) xTaskNotifyGive(s_ws_retry_task);
}

static TimerHandle_t s_ws_retry_timer = NULL;

static void ws_retry_task_ensure(void) {
    if (s_ws_retry_task == NULL) xTaskCreate(ws_retry_task, "ws_retry", 6144, NULL, 4, &s_ws_retry_task);
    if (s_ws_retry_timer == NULL)
        s_ws_retry_timer = xTimerCreate("ws_retry", pdMS_TO_TICKS(5000), pdFALSE, NULL, ws_reconnect_cb);
}

static void ws_schedule_reconnect(void) {
    ws_retry_task_ensure();
    // Обрыв приходит двумя событиями (DISCONNECTED и ERROR): второе не должно удваивать паузу
    if (xTimerIsTimerActive(s_ws_retry_timer)) return;
    int delay_s = 5 << (s_ws_failures < 4 ? s_ws_failures : 4);
    if (delay_s > 10) delay_s = 10;   // было до 60 с: после перезапуска сервера колонка молчала минуту. Лишних запросов это не создаёт (раз в 10 с)
    if (s_ws_failures < 100) s_ws_failures++;
    if (s_ws_failures >= 3) display_error("SERVER ERROR", "check server/token");
    xTimerChangePeriod(s_ws_retry_timer, pdMS_TO_TICKS(delay_s * 1000), 0);
    ESP_LOGW(TAG, "Повторное подключение к серверу через %d с", delay_s);
}

// Wi-Fi вернулся — к серверу сразу, не дожидаясь нарастающей паузы (она нужна при неверном токене/адресе, а не при обрыве сети)
static void ws_reconnect_now(void) {
    if (s_ws == NULL) return;
    ws_retry_task_ensure();
    xTimerStop(s_ws_retry_timer, 0);
    s_ws_failures = 0;
    xTaskNotifyGive(s_ws_retry_task);
}

static void ws_show_idle(void) {
    noctis_settings_t st;
    settings_get(&st);
    esp_netif_ip_info_t ip;
    char ipstr[20] = "";
    esp_netif_t* netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (netif && esp_netif_get_ip_info(netif, &ip) == ESP_OK) snprintf(ipstr, sizeof(ipstr), IPSTR, IP2STR(&ip.ip));
    display_idle(st.wake_word, ipstr, NOCTIS_FIRMWARE_VERSION);
}

static bool control_ws_send(const char* text, int len) { return ws_send(text, len); }

static void websocket_start(void) {
    static char uri[NOCTIS_SERVER_MAX + 32];
    static char headers[NOCTIS_ID_MAX + NOCTIS_TOKEN_MAX + 64];
    noctis_link_t link;
    if (!settings_get_link(&link) || noctis_build_ws_uri(&link, uri, sizeof(uri)) < 0 ||
        noctis_build_auth_headers(&link, headers, sizeof(headers)) < 0) {
        ESP_LOGE(TAG, "Нет ссылки на сервер — нужно сопряжение с приложением");
        return;
    }
    ESP_LOGI(TAG, "WebSocket: %s (устройство %s)", uri, link.device_id);  // токен не логируем

    esp_websocket_client_config_t cfg = {
        .uri = uri,
        .headers = headers,  // X-Device-Id / X-Device-Token
        .buffer_size = 8192,
        .task_stack = 8192,
        .disable_auto_reconnect = true,  // повторы — ws_schedule_reconnect (с нарастающей паузой)
        .network_timeout_ms = 10000,
        .ping_interval_sec = 15,
    };
    s_ws = esp_websocket_client_init(&cfg);
    esp_websocket_register_events(s_ws, WEBSOCKET_EVENT_ANY, ws_event, NULL);
    esp_websocket_client_start(s_ws);
}

// ============================================================
// WI-FI
// ============================================================
static void wifi_event(void* arg, esp_event_base_t base, int32_t id, void* data) {
    // Во время BLE-сопряжения подключением Wi-Fi управляет менеджер сопряжения
    if (provisioning_active()) return;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        // 20 МГц надёжнее 40 МГц в диапазоне 2,4 ГГц (при слабом сигнале авторизация обрывалась по тайм-ауту);
        // без энергосбережения нет скачков задержки — важно для потока музыки и ответов голоса
        esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20);
        esp_wifi_set_ps(WIFI_PS_NONE);
        esp_wifi_set_inactive_time(WIFI_IF_STA, 12);  // пропуск маяков до 12 с (по умолчанию 6 с) не считается обрывом: меньше лишних переподключений
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t* d = (const wifi_event_sta_disconnected_t*)data;
        ESP_LOGW(TAG, "Wi-Fi отключён (причина %d), переподключаюсь...", d ? d->reason : -1);
        display_status("NOCTIS", "Wi-Fi lost", "reconnecting...");
        esp_wifi_connect();
    }
}

static void diag_heap(const char* where);

static void got_ip_event(void* arg, esp_event_base_t base, int32_t id, void* data) {
    ip_event_got_ip_t* e = (ip_event_got_ip_t*)data;
    ESP_LOGI(TAG, "Wi-Fi подключён, IP: " IPSTR, IP2STR(&e->ip_info.ip));
    diag_heap("получен IP");
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) ESP_LOGI(TAG, "Wi-Fi: канал %d, сигнал %d дБм", ap.primary, ap.rssi);
    clock_start_sntp();
    if (!s_ws_started) {
        noctis_link_t link;
        if (settings_get_link(&link)) {
            s_ws_started = true;
            websocket_start();
        }
    } else {
        ws_reconnect_now();
    }
}

static void network_init(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, got_ip_event, NULL));
}

// Сеть и сервер уже сохранены — просто подключаемся; иначе запускаем BLE-сопряжение
static void network_start(void) {
    if (provisioning_is_complete()) {
        wifi_config_t cfg;
        esp_wifi_set_mode(WIFI_MODE_STA);
        esp_wifi_get_config(WIFI_IF_STA, &cfg);
        // Слабый сигнал: быстрый поиск по одной сети часто не находил точку (причина 201) — сканируем все каналы
        cfg.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
        cfg.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
        esp_wifi_set_config(WIFI_IF_STA, &cfg);
        ESP_LOGI(TAG, "Wi-Fi: подключаюсь к сохранённой сети %s", (const char*)cfg.sta.ssid);
        display_status("NOCTIS", "Wi-Fi...", (const char*)cfg.sta.ssid);
        ESP_ERROR_CHECK(esp_wifi_start());
    } else {
        // Ссылка на сервер есть, а сети Wi-Fi нет (сопряжение прервано): сбрасываем и начинаем сопряжение заново
        ESP_LOGW(TAG, "Нет сохранённой сети Wi-Fi — сброс и перезапуск в режим сопряжения");
        settings_factory_reset();
        esp_restart();
    }
}

// Удержание кнопки 5 секунд при включении — сброс к заводским настройкам. Кнопка на GPIO11 (внешняя) работает надёжно;
// BOOT (GPIO0) при включении переводит чип в режим загрузчика, поэтому до этого места прошивка не доходит.
static void check_factory_reset_button(void) {
    netwatch_button_init();
    if (!netwatch_button_pressed()) return;
    display_status("RESET?", "hold button 5s", "to forget setup");
    for (int i = 0; i < 50; i++) {
        vTaskDelay(pdMS_TO_TICKS(100));
        if (!netwatch_button_pressed()) return;
    }
    ESP_LOGW(TAG, "Кнопка удерживалась 5 с — сброс настроек");
    settings_factory_reset();
    display_status("RESET", "done", "");
    vTaskDelay(pdMS_TO_TICKS(1000));
}

// Несопряжённая колонка запускает только BLE-сопряжение, без микрофона, WakeNet и музыки: к моменту сопряжения
// у Bluetooth иначе не хватает внутренней памяти («hci inits failed»). После успешного сопряжения — перезагрузка
// в рабочий режим (Wi-Fi и ссылка на сервер уже в NVS).
static void restart_task(void* arg) {
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();
}

static void pairing_done(void) {
    ESP_LOGI(TAG, "Сопряжение завершено — перезагрузка в рабочий режим");
    xTaskCreate(restart_task, "restart", 2048, NULL, 5, NULL);
}

// Повторная настройка (смена Wi-Fi и/или адреса сервера) без сброса: плата перезагружается в режим Bluetooth, как при первом
// сопряжении, но личность платы (device_id и токен) остаётся. Окно открыто 10 минут, затем плата возвращается к работе.
static void reconfigure_timeout_task(void* arg) {
    vTaskDelay(pdMS_TO_TICKS(10 * 60 * 1000));
    ESP_LOGW(TAG, "Окно повторной настройки закрыто по времени — возврат к работе");
    settings_set_reconfigure(false);
    esp_restart();
}

static void reconfigure_done(void) {
    ESP_LOGI(TAG, "Повторная настройка завершена — перезагрузка в рабочий режим");
    settings_set_reconfigure(false);
    xTaskCreate(restart_task, "restart", 2048, NULL, 5, NULL);
}

static void reconfigure_mode(void) {
    ESP_LOGW(TAG, "Режим повторной настройки по Bluetooth (10 минут); связь с сервером сохранена");
    network_init();
    xTaskCreate(reconfigure_timeout_task, "reconf_to", 2048, NULL, 4, NULL);
    if (provisioning_start(reconfigure_done) != ESP_OK) {
        settings_set_reconfigure(false);
        display_error("BLE ERROR", "restarting");
        vTaskDelay(pdMS_TO_TICKS(2000));
        esp_restart();
    }
}

static void pairing_only_mode(void) {
    ESP_LOGW(TAG, "Устройство не сопряжено — режим сопряжения (микрофон и распознавание выключены)");
    network_init();
    if (provisioning_start(pairing_done) != ESP_OK) {
        display_error("BLE ERROR", "reset board");
    }
}

// Диагностика внутренней памяти: по ней видно, какой этап запуска сколько съедает
static void diag_heap(const char* where) {
    ESP_LOGI(TAG, "[heap] %-14s внутр. свободно %3u КБ, наибольший блок %3u КБ, минимум %3u КБ", where,
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
             (unsigned)(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL) / 1024),
             (unsigned)(heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL) / 1024));
}

// ============================================================
// MAIN
// ============================================================
void app_main(void) {
    ESP_LOGI(TAG, "========================================");
    ESP_LOGI(TAG, "Noctis firmware %s — ESP32-S3 + esp-sr", NOCTIS_FIRMWARE_VERSION);
    ESP_LOGI(TAG, "PSRAM свободно: %u KB", (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));
    ESP_LOGI(TAG, "========================================");

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    diag_heap("старт");
    ESP_ERROR_CHECK(settings_init());
    display_init();  // без экрана работает так же
    ota_init();
    diag_heap("экран, OTA");
    check_factory_reset_button();

    noctis_link_t link;
    if (!settings_get_link(&link)) {
        pairing_only_mode();
        return;
    }
    if (settings_get_reconfigure()) {
        reconfigure_mode();
        return;
    }
    control_init(control_ws_send);

    noctis_settings_t st;
    settings_get(&st);
    audio_set_volume(st.volume);

    s_rec_buf = heap_caps_malloc(MAX_SAMPLES * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    if (s_rec_buf == NULL) {
        ESP_LOGE(TAG, "Нет PSRAM для буфера записи");
        display_error("FATAL", "no PSRAM");
        abort();
    }

    speaker_init();
    s_pb_storage = heap_caps_malloc(PB_RING_BYTES + 1, MALLOC_CAP_SPIRAM);
    s_pcm_tmp = heap_caps_malloc(PCM_TMP_BYTES, MALLOC_CAP_SPIRAM);
    if (s_pb_storage == NULL || s_pcm_tmp == NULL) {
        display_error("FATAL", "no PSRAM");
        abort();
    }
    s_pb = xStreamBufferCreateStatic(PB_RING_BYTES, 1, s_pb_storage, &s_pb_ctrl);
    xTaskCreatePinnedToCore(playback_task, "playback", 4096, NULL, 6, NULL, 1);
    diag_heap("динамик");
    music_init();
    schedule_init();
    diag_heap("музыка+расписание");
    mic_init();
    diag_heap("микрофон");
    afe_init();
    diag_heap("распознавание");

    xTaskCreatePinnedToCore(sender_task, "sender", 6144, NULL, 4, &s_sender_task, 1);
    xTaskCreatePinnedToCore(feed_task, "afe_feed", 12288, NULL, 5, NULL, 0);  // стек под инференс TFLM
    xTaskCreatePinnedToCore(detect_task, "afe_detect", 8192, NULL, 5, NULL, 1);

    play_beep(660, 100);  // устройство запущено
    diag_heap("задачи");
    network_init();
    network_start();
    netwatch_start();
    diag_heap("Wi-Fi запущен");
}
