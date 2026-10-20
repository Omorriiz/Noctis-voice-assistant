#include "ota.h"

#include <stdio.h>
#include <string.h>

#include "control.h"
#include "display.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/sha256.h"
#include "settings.h"

static const char* TAG = "ota";
#define OTA_BUF 4096
#define VALIDATION_TIMEOUT_US (90LL * 1000000LL)  // новая прошивка обязана подключиться к серверу за 90 с

static volatile bool s_busy = false;
static bool s_pending_verify = false;
static esp_timer_handle_t s_validation_timer;

static void validation_expired(void* arg) {
    ESP_LOGE(TAG, "Новая прошивка не подтвердила работу за 90 с — откат");
    esp_ota_mark_app_invalid_rollback_and_reboot();
}

void ota_init(void) {
    const esp_partition_t* running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(running, &state) == ESP_OK && state == ESP_OTA_IMG_PENDING_VERIFY) {
        s_pending_verify = true;
        ESP_LOGW(TAG, "Первый запуск обновления %s: жду подтверждения от сервера", NOCTIS_FIRMWARE_VERSION);
        const esp_timer_create_args_t args = {.callback = validation_expired, .name = "ota_validate"};
        esp_timer_create(&args, &s_validation_timer);
        esp_timer_start_once(s_validation_timer, VALIDATION_TIMEOUT_US);
    }
}

void ota_mark_valid(void) {
    if (!s_pending_verify) return;
    s_pending_verify = false;
    esp_timer_stop(s_validation_timer);
    esp_ota_mark_app_valid_cancel_rollback();
    ESP_LOGI(TAG, "Прошивка %s подтверждена", NOCTIS_FIRMWARE_VERSION);
}

typedef struct {
    noctis_command_t cmd;
} ota_job_t;

static void report(const char* version, const char* state, const char* error) {
    char json[200];
    if (noctis_build_ota_status(json, sizeof(json), version, state, error) > 0) control_send(json);
}

static void ota_task(void* arg) {
    ota_job_t* job = (ota_job_t*)arg;
    const noctis_command_t* cmd = &job->cmd;
    noctis_link_t link;
    char url[NOCTIS_URL_MAX], headers[160], sha_hex[65];
    const char* err = NULL;
    esp_ota_handle_t handle = 0;
    bool began = false;
    uint8_t* buf = NULL;
    esp_http_client_handle_t http = NULL;
    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);

    report(cmd->version, "started", NULL);
    display_status("UPDATING", cmd->version, "do not unplug");

    // Адрес образа строится из сохранённой ссылки на сервер, а не берётся из команды: сообщение
    // не может направить устройство на чужой хост.
    if (!settings_get_link(&link) || noctis_build_ota_url(&link, cmd->version, url, sizeof(url)) < 0 ||
        noctis_build_auth_headers(&link, headers, sizeof(headers)) < 0) {
        err = "no server link";
        goto done;
    }
    const esp_partition_t* part = esp_ota_get_next_update_partition(NULL);
    if (part == NULL) {
        err = "no update partition";
        goto done;
    }
    buf = malloc(OTA_BUF);
    if (buf == NULL) {
        err = "no memory";
        goto done;
    }

    esp_http_client_config_t hc = {.url = url, .timeout_ms = 15000, .crt_bundle_attach = esp_crt_bundle_attach};
    http = esp_http_client_init(&hc);
    if (http == NULL) {
        err = "http init";
        goto done;
    }
    // Заголовки передаём по одному (две строки X-Device-*)
    char id_h[NOCTIS_ID_MAX + 1], tok_h[NOCTIS_TOKEN_MAX + 1];
    snprintf(id_h, sizeof(id_h), "%s", link.device_id);
    snprintf(tok_h, sizeof(tok_h), "%s", link.token);
    esp_http_client_set_header(http, "X-Device-Id", id_h);
    esp_http_client_set_header(http, "X-Device-Token", tok_h);

    if (esp_http_client_open(http, 0) != ESP_OK) {
        err = "connect failed";
        goto done;
    }
    int64_t total = esp_http_client_fetch_headers(http);
    int status = esp_http_client_get_status_code(http);
    if (status != 200) {
        snprintf((char*)buf, OTA_BUF, "http %d", status);
        err = (const char*)buf;
        goto done;
    }
    if (total <= 0 || (uint64_t)total > part->size || (cmd->size > 0 && total != cmd->size)) {
        err = "bad image size";
        goto done;
    }

    if (esp_ota_begin(part, OTA_WITH_SEQUENTIAL_WRITES, &handle) != ESP_OK) {
        err = "ota begin";
        goto done;
    }
    began = true;
    mbedtls_sha256_starts(&sha, 0);

    int64_t got = 0;
    int last_pct = -10;
    while (got < total) {
        int n = esp_http_client_read(http, (char*)buf, OTA_BUF);
        if (n < 0) {
            err = "download error";
            goto done;
        }
        if (n == 0) {
            if (esp_http_client_is_complete_data_received(http)) break;
            err = "connection lost";
            goto done;
        }
        mbedtls_sha256_update(&sha, buf, n);
        if (esp_ota_write(handle, buf, n) != ESP_OK) {
            err = "flash write";
            goto done;
        }
        got += n;
        int pct = (int)(got * 100 / total);
        if (pct >= last_pct + 10) {
            last_pct = pct;
            char line[24];
            snprintf(line, sizeof(line), "%d%%", pct);
            display_status("UPDATING", cmd->version, line);
        }
    }
    if (got != total) {
        err = "incomplete";
        goto done;
    }

    uint8_t digest[32];
    mbedtls_sha256_finish(&sha, digest);
    noctis_sha256_to_hex(digest, sha_hex);
    if (strcasecmp(sha_hex, cmd->sha256) != 0) {  // проверка ДО переключения слота
        ESP_LOGE(TAG, "SHA-256 не совпал: получено %s, ожидалось %s", sha_hex, cmd->sha256);
        err = "sha256 mismatch";
        goto done;
    }
    esp_err_t e = esp_ota_end(handle);
    began = false;
    if (e != ESP_OK) {
        err = "image validation failed";
        goto done;
    }
    if (esp_ota_set_boot_partition(part) != ESP_OK) {
        err = "set boot partition";
        goto done;
    }
    ESP_LOGI(TAG, "Обновление %s записано, перезагрузка", cmd->version);
    report(cmd->version, "ok", NULL);
    display_status("UPDATED", cmd->version, "rebooting...");
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();

done:
    if (began) esp_ota_abort(handle);
    if (http) {
        esp_http_client_close(http);
        esp_http_client_cleanup(http);
    }
    if (err) {
        ESP_LOGE(TAG, "OTA не выполнено: %s (остаёмся на %s)", err, NOCTIS_FIRMWARE_VERSION);
        report(cmd->version, "failed", err);
        display_error("UPDATE FAILED", err);
    }
    mbedtls_sha256_free(&sha);
    free(buf);
    free(job);
    s_busy = false;
    vTaskDelete(NULL);
}

bool ota_start(const noctis_command_t* cmd) {
    if (s_busy) {
        ESP_LOGW(TAG, "OTA уже выполняется");
        return false;
    }
    // Только обновление вперёд: понижение версии командой не допускается
    if (noctis_version_compare(cmd->version, NOCTIS_FIRMWARE_VERSION) <= 0) {
        ESP_LOGW(TAG, "Версия %s не новее текущей %s — пропуск", cmd->version, NOCTIS_FIRMWARE_VERSION);
        char json[200];
        if (noctis_build_ota_status(json, sizeof(json), cmd->version, "failed", "not newer") > 0) control_send(json);
        return false;
    }
    ota_job_t* job = malloc(sizeof(*job));
    if (job == NULL) return false;
    job->cmd = *cmd;
    s_busy = true;
    if (xTaskCreate(ota_task, "ota", 8192, job, 4, NULL) != pdPASS) {
        s_busy = false;
        free(job);
        return false;
    }
    return true;
}
