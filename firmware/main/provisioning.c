#include "provisioning.h"

#include <stdio.h>
#include <string.h>

#include "display.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "settings.h"
#include "wifi_provisioning/manager.h"
#include "wifi_provisioning/scheme_ble.h"

static const char* TAG = "prov";
#define LINK_ENDPOINT "noctis-config"

static char s_name[20];
static char s_pop[16];
static void (*s_on_done)(void) = NULL;
static volatile bool s_active = false;

bool provisioning_active(void) { return s_active; }

const char* provisioning_service_name(void) { return s_name; }

bool provisioning_is_complete(void) {
    bool prov = false;
    wifi_prov_mgr_is_provisioned(&prov);
    noctis_link_t link;
    return prov && settings_get_link(&link);
}

/**
 * Endpoint "noctis-config": приложение присылает JSON {"server","tls","device_id","token"}
 * (device_id и token выдаёт сервер при создании устройства). Вызывать ДО отправки данных Wi-Fi.
 */
static esp_err_t link_handler(uint32_t session_id, const uint8_t* in, ssize_t in_len, uint8_t** out, ssize_t* out_len,
                              void* priv) {
    noctis_link_t link;
    const char* reply;
    // Первое сопряжение: приходят server, device_id и token. Повторная настройка: достаточно server (и tls) —
    // device_id и token платы сохраняются (приложение их не знает: секрет показывается только при создании).
    const bool have_existing = settings_get_link(&link);
    if (in != NULL && in_len > 0 && noctis_parse_link_update((const char*)in, (size_t)in_len, &link, have_existing) &&
        settings_set_link(&link) == ESP_OK) {
        ESP_LOGI(TAG, "Получена ссылка на сервер %s, устройство %s", link.server, link.device_id);
        reply = "{\"status\":\"ok\"}";
    } else {
        ESP_LOGW(TAG, "Некорректные данные сервера от приложения");
        reply = "{\"status\":\"error\"}";
    }
    *out = (uint8_t*)strdup(reply);
    *out_len = *out ? (ssize_t)strlen(reply) : 0;
    return *out ? ESP_OK : ESP_ERR_NO_MEM;
}

static void restart_task(void* arg) {
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();
}

static void prov_event(void* arg, esp_event_base_t base, int32_t id, void* data) {
    if (base != WIFI_PROV_EVENT) return;
    switch (id) {
        case WIFI_PROV_START:
            ESP_LOGI(TAG, "BLE-сопряжение запущено: %s, код %s", s_name, s_pop);
            display_pairing(s_name, s_pop);
            break;
        case WIFI_PROV_CRED_RECV: {
            wifi_sta_config_t* c = (wifi_sta_config_t*)data;
            ESP_LOGI(TAG, "Получена сеть Wi-Fi: %s", (const char*)c->ssid);  // пароль не логируем
            display_status("Noctis", "Wi-Fi...", "");
            break;
        }
        case WIFI_PROV_CRED_FAIL:
            ESP_LOGE(TAG, "Не удалось подключиться к Wi-Fi (неверный пароль или сеть недоступна)");
            display_error("Wi-Fi ERROR", "Check password");
            break;
        case WIFI_PROV_CRED_SUCCESS:
            ESP_LOGI(TAG, "Wi-Fi подключён");
            break;
        case WIFI_PROV_END: {
            s_active = false;
            wifi_prov_mgr_deinit();
            noctis_link_t link;
            if (!settings_get_link(&link)) {
                // Приложение не прислало сервер: без него устройство бесполезно. Забываем сеть и начинаем заново.
                ESP_LOGE(TAG, "Сопряжение без ссылки на сервер — сброс сети, перезапуск");
                esp_wifi_restore();
                xTaskCreate(restart_task, "restart", 2048, NULL, 5, NULL);
            } else if (s_on_done) {
                s_on_done();
            }
            break;
        }
        default:
            break;
    }
}

esp_err_t provisioning_start(void (*on_done)(void)) {
    s_on_done = on_done;
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(s_name, sizeof(s_name), "NOCTIS_%02X%02X%02X", mac[3], mac[4], mac[5]);
    settings_get_pop(s_pop);

    ESP_LOGI(TAG, "Внутренняя память перед BLE: свободно %u КБ, наибольший блок %u КБ",
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
             (unsigned)(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL) / 1024));
    s_active = true;
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_PROV_EVENT, ESP_EVENT_ANY_ID, prov_event, NULL));

    wifi_prov_mgr_config_t cfg = {
        .scheme = wifi_prov_scheme_ble,
        .scheme_event_handler = WIFI_PROV_SCHEME_BLE_EVENT_HANDLER_FREE_BTDM,  // память BLE освобождается после
    };
    ESP_ERROR_CHECK(wifi_prov_mgr_init(cfg));

    wifi_prov_mgr_endpoint_create(LINK_ENDPOINT);
    esp_err_t err = wifi_prov_mgr_start_provisioning(WIFI_PROV_SECURITY_1, s_pop, s_name, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Не удалось запустить сопряжение: %s", esp_err_to_name(err));
        return err;
    }
    wifi_prov_mgr_endpoint_register(LINK_ENDPOINT, link_handler, NULL);
    return ESP_OK;
}
