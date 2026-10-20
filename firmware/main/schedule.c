#include "schedule.h"

#include <stdio.h>
#include <string.h>

#include "audio_out.h"
#include "clock.h"
#include "control.h"
#include "display.h"
#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "music.h"
#include "noctis_sched.h"
#include "nvs.h"
#include "settings.h"

#define POLL_MS 500
#define RING_MAX_MS 60000          // звонок сам замолкает через минуту (как и на сервере)
#define BUTTON_GPIO GPIO_NUM_0     // BOOT: короткое нажатие останавливает звонок

static const char* TAG = "schedule";
static sched_state_t s_state;
static SemaphoreHandle_t s_lock;
static volatile bool s_ringing = false;
static volatile bool s_ring_wanted = false;
static char s_melody[SCHED_MELODY_MAX] = "default";
static TaskHandle_t s_ring_task = NULL;
// Большие буферы — статические, а не на стеке: load() работает в main_task (стек 3,5 КБ), и локальные массивы
// ~2,5 КБ переполняли его, портя кучу («assert failed: block_locate_free» при следующем malloc).
// Обращаться к ним можно только под s_lock (либо до старта задач, как в load()).
static uint8_t s_blob[SCHED_BLOB_MAX];
static sched_state_t s_loaded;
static sched_state_t s_next;

/* ---------------------------------------------------------------- NVS */
static void save_locked(void) {
    const size_t n = sched_serialize(&s_state, s_blob, sizeof(s_blob));
    nvs_handle_t h;
    if (n == 0 || nvs_open("noctis", NVS_READWRITE, &h) != ESP_OK) return;
    nvs_set_blob(h, "sched", s_blob, n);
    nvs_commit(h);
    nvs_close(h);
}

static void load(void) {
    sched_init(&s_state);
    nvs_handle_t h;
    if (nvs_open("noctis", NVS_READONLY, &h) != ESP_OK) return;
    size_t n = sizeof(s_blob);
    if (nvs_get_blob(h, "sched", s_blob, &n) == ESP_OK) {
        if (sched_deserialize(s_blob, n, &s_loaded)) {
            s_state = s_loaded;
            ESP_LOGI(TAG, "Расписание из NVS: таймеров %d, будильников %d", s_state.n_timers, s_state.n_alarms);
        } else {
            ESP_LOGW(TAG, "Расписание в NVS повреждено — игнорирую (сервер пришлёт заново)");
        }
    }
    nvs_close(h);
}

void schedule_apply(const char* json, size_t len) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (sched_parse(json, len, &s_state, &s_next)) {
        s_state = s_next;
        save_locked();
        ESP_LOGI(TAG, "Расписание обновлено (rev %d): таймеров %d, будильников %d", s_state.rev, s_state.n_timers,
                 s_state.n_alarms);
    } else {
        ESP_LOGW(TAG, "Неверное сообщение schedule — игнорирую");
    }
    xSemaphoreGive(s_lock);
}

/* ---------------------------------------------------------------- звонок */
bool schedule_ringing(void) { return s_ringing; }

void schedule_stop_ring(bool notify_server) {
    const bool was = s_ring_wanted;
    s_ring_wanted = false;
    if (was && notify_server) {
        char json[48];
        if (sched_build_ring_stopped(json, sizeof(json)) > 0) control_send(json);
    }
}

static void ring_task(void* arg) {
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        s_ringing = true;
        const int64_t start = esp_timer_get_time() / 1000;
        while (s_ring_wanted && esp_timer_get_time() / 1000 - start < RING_MAX_MS) {
            for (int step = 0; s_ring_wanted; step++) {
                int freq, ms;
                if (!noctis_melody_note(s_melody, step, &freq, &ms)) break;     // паттерн закончился — повторить
                if (freq > 0) audio_play_tone(freq, ms, true);
                else vTaskDelay(pdMS_TO_TICKS(ms));
            }
        }
        s_ring_wanted = false;
        s_ringing = false;
    }
}

static void start_ring(const char* melody) {
    if (s_ring_wanted) return;                   // уже звонит
    noctis_copy(s_melody, sizeof(s_melody), noctis_is_valid_melody(melody) ? melody : "default");
    music_stop_local();                          // музыка на время звонка замолкает (сервер ставит её на паузу)
    s_ring_wanted = true;
    xTaskNotifyGive(s_ring_task);
}

/* ---------------------------------------------------------------- главный экран */
static void update_home(void) {
    noctis_settings_t st;
    settings_get(&st);
    char hhmm[8] = "--:--", l1[24], l2[24] = "", l3[24] = "";
    if (clock_valid()) noctis_format_clock(clock_now(), clock_offset(), hhmm, sizeof(hhmm));
    snprintf(l1, sizeof(l1), "Say: %.16s", strncmp(st.wake_word, "custom:", 7) == 0 ? st.wake_word + 7 : st.wake_word);

    int left, ah, am;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    sched_summary(&s_state, clock_now(), clock_offset(), &left, &ah, &am);
    xSemaphoreGive(s_lock);
    if (left >= 0) snprintf(l2, sizeof(l2), "Timer %02d:%02d", left / 60 % 100, left % 60);
    if (ah >= 0) snprintf(l3, sizeof(l3), "Alarm %02d:%02d", ah, am);
    if (music_active()) snprintf(l3, sizeof(l3), "> %.18s", music_title());      // кириллица шрифтом не поддерживается ('?')
    display_home(hhmm, l1, l2, l3);
}

/* ---------------------------------------------------------------- опрос */
static unsigned stack_left(const char* name) {
    TaskHandle_t h = xTaskGetHandle(name);
    return h ? (unsigned)uxTaskGetStackHighWaterMark(h) : 0;
}

static void poll_task(void* arg) {
    gpio_config_t io = {.pin_bit_mask = 1ULL << BUTTON_GPIO, .mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE};
    gpio_config(&io);
    int ticks = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(POLL_MS));
        if (gpio_get_level(BUTTON_GPIO) == 0 && s_ring_wanted) {
            ESP_LOGI(TAG, "Звонок остановлен кнопкой");
            schedule_stop_ring(true);
        }
        sched_event_t ev[4];
        xSemaphoreTake(s_lock, portMAX_DELAY);
        const int n = sched_poll(&s_state, clock_now(), clock_offset(), ev, 4);
        if (n > 0) save_locked();                // запомнить, что сработало (не повторять после перезагрузки)
        xSemaphoreGive(s_lock);
        for (int i = 0; i < n; i++) {
            ESP_LOGW(TAG, "Сработал %s #%d «%s»", ev[i].kind == SCHED_EVT_TIMER ? "таймер" : "будильник", (int)ev[i].id, ev[i].label);
            char json[96];
            if (sched_build_event(json, sizeof(json), ev[i].kind, ev[i].id) > 0) control_send(json);   // сервер озвучит
            display_status(ev[i].kind == SCHED_EVT_TIMER ? "TIMER" : "ALARM", ev[i].label, "BOOT = stop");
            start_ring(ev[i].melody);
        }
        if (++ticks % 2 == 0) update_home();     // раз в секунду
        if (ticks % 120 == 0) {                  // раз в минуту: память и запас стека задач (ранние признаки утечки/переполнения)
            ESP_LOGI(TAG, "Память: свободно %u КБ (мин. %u КБ); запас стека, Б: sched %u feed %u detect %u sender %u ws %u evt %u tmr %u",
                     (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
                     (unsigned)(heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL) / 1024),
                     (unsigned)uxTaskGetStackHighWaterMark(NULL), stack_left("afe_feed"), stack_left("afe_detect"),
                     stack_left("sender"), stack_left("websocket_task"), stack_left("sys_evt"), stack_left("Tmr Svc"));
        }
    }
}

void schedule_init(void) {
    s_lock = xSemaphoreCreateMutex();
    load();
    xTaskCreatePinnedToCore(ring_task, "ring", 3072, NULL, 3, &s_ring_task, 1);
    xTaskCreatePinnedToCore(poll_task, "sched", 4096, NULL, 3, NULL, 1);
}
