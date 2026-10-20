/** Хранилище настроек в NVS (namespace "noctis"). Потокобезопасно. */
#pragma once

#include <stdbool.h>

#include "esp_err.h"
#include "noctis_core.h"

esp_err_t settings_init(void);  // после nvs_flash_init()

/** Копия текущих настроек (персонализация). */
void settings_get(noctis_settings_t* out);

/** Применяет настройки, пришедшие с сервера, и сохраняет в NVS. true — что-то изменилось. */
bool settings_apply(const noctis_settings_t* incoming, bool* wake_word_changed);

/** Громкость сразу, без ожидания сервера (например, локальная кнопка). */
void settings_set_volume(int volume);

bool settings_get_link(noctis_link_t* out);  // false, если устройство не сопряжено с сервером
esp_err_t settings_set_link(const noctis_link_t* link);

/** Флаг «следующая загрузка — повторная настройка по Bluetooth» (связь с сервером и личность платы сохраняются). */
bool settings_get_reconfigure(void);
void settings_set_reconfigure(bool on);

/** Код сопряжения (PoP) для BLE: создаётся случайно при первом запуске, показывается на экране. */
void settings_get_pop(char out[16]);

/** Стирает всё (Wi-Fi, ссылка на сервер, персонализация, PoP). */
void settings_factory_reset(void);
