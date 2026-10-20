#include "netwatch.h"

#include <string.h>

#include "control.h"
#include "display.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "schedule.h"
#include "settings.h"

static const char* TAG = "netwatch";

#define WIFI_SWITCH_TIMEOUT_MS 45000    // на подключение к новой сети и связь с сервером
#define OFFLINE_HINT_MS (2 * 60 * 1000)   // подсказка на экране: «удерживайте BOOT»
#define OFFLINE_AUTO_MS (10 * 60 * 1000)  // сервер недоступен так долго: открыть окно Bluetooth-настройки
#define BUTTON_HOLD_MS 3000               // долгое нажатие кнопки во время работы
#define BUTTON_GPIO GPIO_NUM_0            // кнопка BOOT на плате
#define BUTTON2_GPIO GPIO_NUM_11          // внешняя кнопка: GPIO11 — GND (внутренняя подтяжка к питанию включена, резистор не нужен)

void netwatch_button_init(void) {
    gpio_config_t io = {.pin_bit_mask = (1ULL << BUTTON_GPIO) | (1ULL << BUTTON2_GPIO), .mode = GPIO_MODE_INPUT,
                        .pull_up_en = GPIO_PULLUP_ENABLE};
    gpio_config(&io);
}

// Нажата любая из двух кнопок: BOOT (GPIO0) или внешняя (GPIO11)
bool netwatch_button_pressed(void) { return gpio_get_level(BUTTON_GPIO) == 0 || gpio_get_level(BUTTON2_GPIO) == 0; }

static noctis_wifi_switch_t s_sw;
static wifi_config_t s_old_cfg;
static bool s_have_old = false;
static noctis_offline_t s_off_hint, s_off_auto;
static bool s_reconfig_requested = false;

static int64_t now_ms64(void) { return esp_timer_get_time() / 1000; }

static void restart_later(void* arg) {
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();
}

void netwatch_enter_reconfigure(const char* reason) {
    if (s_reconfig_requested) return;
    s_reconfig_requested = true;
    ESP_LOGW(TAG, "Повторная настройка по Bluetooth: %s", reason);
    display_status("RECONFIGURE", "restarting...", "");
    settings_set_reconfigure(true);
    xTaskCreate(restart_later, "reconf", 2048, NULL, 5, NULL);
}

bool netwatch_wifi_change(const noctis_command_t* cmd) {
    if (s_sw.trying) return false;
    wifi_config_t next;
    if (esp_wifi_get_config(WIFI_IF_STA, &s_old_cfg) != ESP_OK) return false;
    s_have_old = true;
    next = s_old_cfg;
    memset(next.sta.ssid, 0, sizeof(next.sta.ssid));
    memset(next.sta.password, 0, sizeof(next.sta.password));
    memcpy(next.sta.ssid, cmd->ssid, strnlen(cmd->ssid, sizeof(next.sta.ssid)));
    memcpy(next.sta.password, cmd->password, strnlen(cmd->password, sizeof(next.sta.password)));
    next.sta.threshold.authmode = WIFI_AUTH_OPEN;  // принимаем любую защиту, какую предложит точка
    next.sta.bssid_set = false;                    // другая сеть — другая точка
    ESP_LOGW(TAG, "Смена сети Wi-Fi на «%s» (при неудаче вернусь в «%s»)", (const char*)next.sta.ssid, (const char*)s_old_cfg.sta.ssid);
    display_status("NOCTIS", "Wi-Fi...", (const char*)next.sta.ssid);
    noctis_wifi_switch_begin(&s_sw, cmd->request_id, now_ms64(), WIFI_SWITCH_TIMEOUT_MS);
    esp_wifi_set_config(WIFI_IF_STA, &next);
    memset(&next, 0, sizeof(next));  // пароль в памяти не оставляем
    esp_wifi_disconnect();           // обработчик Wi-Fi переподключится уже с новой сетью
    return true;
}

static void rollback(void) {
    if (s_have_old) {
        ESP_LOGW(TAG, "Новая сеть не сработала — возвращаюсь в «%s»", (const char*)s_old_cfg.sta.ssid);
        esp_wifi_set_config(WIFI_IF_STA, &s_old_cfg);
        esp_wifi_disconnect();
        vTaskDelay(pdMS_TO_TICKS(200));
        esp_wifi_connect();
    }
}

static void forget_old(void) {
    memset(&s_old_cfg, 0, sizeof(s_old_cfg));
    s_have_old = false;
}

static void watch_task(void* arg) {
    int held_ms = 0;
    noctis_offline_init(&s_off_hint);
    noctis_offline_init(&s_off_auto);
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        const int64_t now = now_ms64();
        const bool server_ok = net_server_connected();

        // 1. Итог смены сети
        const noctis_wifi_decision_t d = noctis_wifi_switch_poll(&s_sw, now, server_ok);
        if (d == NOCTIS_WIFI_COMMIT) {
            ESP_LOGI(TAG, "Новая сеть Wi-Fi работает");
            forget_old();
        } else if (d == NOCTIS_WIFI_ROLLBACK) {
            rollback();
            forget_old();
        }
        bool ok;
        char rid[NOCTIS_REQID_MAX];
        if (noctis_wifi_switch_take_report(&s_sw, server_ok, &ok, rid)) {
            char json[160];
            if (noctis_build_wifi_result(json, sizeof(json), rid, ok, ok ? NULL : "connect_failed") > 0) control_send(json);
        }

        // 2. Долго нет связи с сервером: подсказка, затем окно Bluetooth-настройки
        if (!s_sw.trying) {
            if (noctis_offline_poll(&s_off_hint, now, server_ok, OFFLINE_HINT_MS))
                display_status("OFFLINE", "hold button 3s", "to reconfigure");
            if (noctis_offline_poll(&s_off_auto, now, server_ok, OFFLINE_AUTO_MS)) netwatch_enter_reconfigure("нет связи с сервером 10 минут");
        } else {
            noctis_offline_init(&s_off_hint);
            noctis_offline_init(&s_off_auto);
        }

        // 3. Долгое нажатие кнопки (BOOT или внешней на GPIO11) во время работы (не во время звонка)
        if (netwatch_button_pressed() && !schedule_ringing()) {
            held_ms += 1000;
            if (held_ms >= BUTTON_HOLD_MS) netwatch_enter_reconfigure("долгое нажатие кнопки");
        } else {
            held_ms = 0;
        }
    }
}

void netwatch_start(void) {
    netwatch_button_init();
    xTaskCreate(watch_task, "netwatch", 4096, NULL, 3, NULL);
}
