#include "clock.h"

#include <sys/time.h>
#include <time.h>

#include "esp_log.h"
#include "esp_sntp.h"
#include "noctis_sched.h"
#include "nvs.h"

static const char* TAG = "clock";
static volatile int32_t s_offset = 0;
static bool s_sntp_started = false;

static void load_offset(void) {
    nvs_handle_t h;
    int32_t v = 0;
    if (nvs_open("noctis", NVS_READONLY, &h) == ESP_OK) {
        nvs_get_i32(h, "utcoff", &v);
        nvs_close(h);
    }
    s_offset = v;
}

static void save_offset(int32_t v) {
    nvs_handle_t h;
    if (nvs_open("noctis", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_i32(h, "utcoff", v);
        nvs_commit(h);
        nvs_close(h);
    }
}

static void sntp_synced(struct timeval* tv) { ESP_LOGI(TAG, "SNTP: время получено (%lld)", (long long)tv->tv_sec); }

void clock_start_sntp(void) {
    if (s_sntp_started) return;
    s_sntp_started = true;
    load_offset();
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "pool.ntp.org");
    esp_sntp_setservername(1, "time.cloudflare.com");
    sntp_set_time_sync_notification_cb(sntp_synced);
    esp_sntp_init();
    ESP_LOGI(TAG, "SNTP запущен, смещение пояса %d с", (int)s_offset);
}

void clock_apply_server(int64_t epoch, int32_t utc_offset) {
    if (!s_sntp_started) load_offset();
    if (utc_offset != s_offset) {
        s_offset = utc_offset;
        save_offset(utc_offset);
    }
    if (sntp_get_sync_status() == SNTP_SYNC_STATUS_COMPLETED) return;      // NTP точнее
    struct timeval tv = {.tv_sec = (time_t)epoch, .tv_usec = 0};
    settimeofday(&tv, NULL);
    ESP_LOGI(TAG, "Время установлено по серверу: %lld", (long long)epoch);
}

int64_t clock_now(void) { return (int64_t)time(NULL); }
int32_t clock_offset(void) { return s_offset; }
bool clock_valid(void) { return noctis_time_valid(clock_now()); }
