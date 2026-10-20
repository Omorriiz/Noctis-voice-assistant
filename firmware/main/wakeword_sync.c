#include "wakeword_sync.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "control.h"
#include "display.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "noctis_core.h"
#include "nww.h"
#include "settings.h"

static const char* TAG = "wake_sync";

static volatile bool s_busy = false;
static char s_failed_sha[65] = "";  // не повторять бесконечно скачивание, которое не удалось

typedef struct {
    char sha[65];
    int size;
} sync_job_t;

static void sync_task(void* arg) {
    sync_job_t* job = (sync_job_t*)arg;
    noctis_link_t link;
    char url[NOCTIS_URL_MAX];
    const char* err = NULL;
    uint8_t* buf = NULL;
    esp_http_client_handle_t http = NULL;

    display_status("WAKE MODEL", "downloading...", "");
    if (!settings_get_link(&link) || noctis_build_wakeword_url(&link, job->sha, url, sizeof(url)) < 0) {
        err = "no server link";
        goto done;
    }
    buf = heap_caps_malloc(NWWM_MAX_TOTAL, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
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
    esp_http_client_set_header(http, "X-Device-Id", link.device_id);
    esp_http_client_set_header(http, "X-Device-Token", link.token);
    if (esp_http_client_open(http, 0) != ESP_OK) {
        err = "connect failed";
        goto done;
    }
    int64_t total = esp_http_client_fetch_headers(http);
    if (esp_http_client_get_status_code(http) != 200 || total <= 0 || total > NWWM_MAX_TOTAL ||
        (job->size > 0 && total != job->size)) {
        err = "bad response";
        goto done;
    }
    int64_t got = 0;
    while (got < total) {
        int n = esp_http_client_read(http, (char*)buf + got, (int)(total - got));
        if (n <= 0) {
            err = "download error";
            goto done;
        }
        got += n;
    }

    // Проверка ДО записи: хэш содержимого = хэшу из config, затем структура контейнера
    uint8_t digest[32];
    char hex[65];
    noctis_sha256(buf, (size_t)total, digest);
    noctis_sha256_to_hex(digest, hex);
    if (strcmp(hex, job->sha) != 0) {
        err = "sha256 mismatch";
        goto done;
    }
    nww_container_t c;
    const char* cerr = NULL;
    if (!nww_container_parse(buf, (size_t)total, &c, &cerr)) {
        ESP_LOGE(TAG, "Контейнер некорректен: %s", cerr ? cerr : "?");
        err = "bad container";
        goto done;
    }
    if (!nww_partition_write(buf, (size_t)total)) {
        err = "flash write failed";
        goto done;
    }
    ESP_LOGI(TAG, "Модель «%s» записана (%d байт), перезагрузка", c.manifest.label, (int)total);
    display_status("WAKE MODEL", c.manifest.label, "installed, restart");
    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();

done:
    if (http) {
        esp_http_client_close(http);
        esp_http_client_cleanup(http);
    }
    if (err) {
        ESP_LOGE(TAG, "Модель слова не установлена: %s", err);
        snprintf(s_failed_sha, sizeof(s_failed_sha), "%s", job->sha);
        display_error("WAKE MODEL", err);
    }
    heap_caps_free(buf);
    free(job);
    s_busy = false;
    vTaskDelete(NULL);
}

bool wakeword_sync_start(const char* sha256_hex, int size) {
    if (s_busy || sha256_hex == NULL || strlen(sha256_hex) != 64) return false;
    if (strcmp(sha256_hex, s_failed_sha) == 0) {
        ESP_LOGW(TAG, "Скачивание этой модели уже не удавалось — жду следующего config");
        return false;
    }
    sync_job_t* job = malloc(sizeof(*job));
    if (job == NULL) return false;
    snprintf(job->sha, sizeof(job->sha), "%s", sha256_hex);
    job->size = size;
    s_busy = true;
    if (xTaskCreate(sync_task, "wake_sync", 8192, job, 4, NULL) != pdPASS) {
        s_busy = false;
        free(job);
        return false;
    }
    return true;
}
