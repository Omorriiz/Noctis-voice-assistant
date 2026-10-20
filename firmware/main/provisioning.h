/** BLE-сопряжение: Wi-Fi (wifi_provisioning, security 1 + PoP) и ссылка на сервер (endpoint "noctis-config"). */
#pragma once

#include <stdbool.h>

#include "esp_err.h"

/** true, если в NVS есть и сеть Wi-Fi, и ссылка на сервер. */
bool provisioning_is_complete(void);

/**
 * Запускает BLE-сопряжение. Имя BLE-устройства: NOCTIS_XXXXXX (последние байты MAC), код (PoP)
 * показывается на экране и в логе. После получения Wi-Fi и ссылки на сервер вызывает on_done
 * (Wi-Fi уже подключается). Без ссылки сопряжение считается незавершённым и перезапускается.
 */
esp_err_t provisioning_start(void (*on_done)(void));

/** Имя BLE-устройства (для экрана). */
const char* provisioning_service_name(void);

/** true, пока идёт BLE-сопряжение (подключением Wi-Fi управляет менеджер сопряжения). */
bool provisioning_active(void);
