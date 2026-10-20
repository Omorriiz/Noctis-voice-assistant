#include "control.h"

#include <string.h>

#include "display.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "netwatch.h"
#include "ota.h"
#include "schedule.h"
#include "wakeword_sync.h"
#include "settings.h"

static const char* TAG = "control";
static bool (*s_send)(const char*, int) = NULL;

void control_init(bool (*send)(const char*, int)) { s_send = send; }

bool control_send(const char* json) { return s_send && s_send(json, (int)strlen(json)); }

static const char* reset_reason_name(void) {
    switch (esp_reset_reason()) {
        case ESP_RST_POWERON: return "poweron";
        case ESP_RST_SW: return "software";
        case ESP_RST_PANIC: return "panic";
        case ESP_RST_BROWNOUT: return "brownout";
        case ESP_RST_TASK_WDT:
        case ESP_RST_INT_WDT:
        case ESP_RST_WDT: return "watchdog";
        default: return "other";
    }
}

void control_send_device_info(void) {
    noctis_settings_t s;
    settings_get(&s);
    char json[200];
    if (noctis_build_device_info(json, sizeof(json), s.revision, (unsigned)esp_get_free_heap_size(),
                                 reset_reason_name()) > 0) {
        control_send(json);
    }
}

void control_schedule_restart(void);

static void reboot_task(void* arg) {
    vTaskDelay(pdMS_TO_TICKS(500));  // дать уйти ответу
    if (arg) settings_factory_reset();
    esp_restart();
}

void control_schedule_restart(void) { xTaskCreate(reboot_task, "restart", 3072, NULL, 5, NULL); }

void control_handle(const char* json, size_t len) {
    noctis_settings_t cur;
    settings_get(&cur);
    noctis_settings_t incoming = cur;
    noctis_command_t cmd;
    memset(&cmd, 0, sizeof(cmd));

    switch (noctis_parse_server_message(json, len, &incoming, &cmd)) {
        case NOCTIS_MSG_CONFIG: {
            bool wake_changed = false;
            if (settings_apply(&incoming, &wake_changed)) {
                noctis_settings_t now;
                settings_get(&now);
                ESP_LOGI(TAG, "Настройки обновлены (rev %d): слово=%s голос=%s характер=%s язык=%s громкость=%d",
                         now.revision, now.wake_word, now.voice_gender, now.character_id, now.language, now.volume);
                audio_set_volume(now.volume);
                ESP_LOGI(TAG, "Слово пробуждения: %s", now.wake_word);
            }
            ota_mark_valid();  // сервер ответил и принял устройство — прошивка рабочая

            // Модель microWakeWord: если слову нужна модель и у сервера другая версия — скачать, записать, перезагрузиться
            char sha[65];
            int size = 0;
            noctis_settings_t cur_now;
            settings_get(&cur_now);
            if (noctis_parse_wake_model(json, len, sha, &size) &&
                noctis_wake_model_needed(cur_now.wake_word, audio_installed_wake_sha(), sha)) {
                ESP_LOGW(TAG, "Нужна модель слова «%s» (sha %.12s…, %d байт)", cur_now.wake_word, sha, size);
                if (wakeword_sync_start(sha, size)) break;  // после записи перезагрузка сама
            }
            // Слово сменилось (другой движок или модель): перезапуск, чтобы подхватить новое слово
            if (wake_changed) {
                ESP_LOGW(TAG, "Слово пробуждения изменено на «%s» — перезапуск", cur_now.wake_word);
                display_status("WAKE WORD", cur_now.wake_word, "restarting...");
                control_schedule_restart();
            }
            break;
        }
        case NOCTIS_MSG_COMMAND:
            if (cmd.cmd == NOCTIS_CMD_REBOOT) {
                ESP_LOGW(TAG, "Команда: перезагрузка");
                display_status("REBOOT", "", "");
                xTaskCreate(reboot_task, "reboot", 3072, NULL, 5, NULL);
            } else if (cmd.cmd == NOCTIS_CMD_FACTORY_RESET) {
                ESP_LOGW(TAG, "Команда: сброс к заводским настройкам");
                display_status("RESET", "factory reset", "");
                xTaskCreate(reboot_task, "reset", 3072, (void*)1, 5, NULL);
            } else if (cmd.cmd == NOCTIS_CMD_STOP_RING) {
                schedule_stop_ring(false);
            } else if (cmd.cmd == NOCTIS_CMD_SET_WIFI) {
                if (!netwatch_wifi_change(&cmd)) {
                    char out[120];
                    if (noctis_build_wifi_result(out, sizeof(out), cmd.request_id, false, "busy") > 0) control_send(out);
                }
                memset(cmd.password, 0, sizeof(cmd.password));
            } else if (cmd.cmd == NOCTIS_CMD_OTA) {
                ESP_LOGW(TAG, "Команда: OTA до версии %s", cmd.version);
                ota_start(&cmd);
            } else {
                ESP_LOGW(TAG, "Неизвестная или некорректная команда — игнорирую");
            }
            break;
        default:
            break;
    }
}
