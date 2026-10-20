/** Сеть без сброса платы: безопасная смена Wi-Fi по команде сервера (с откатом), окно повторной настройки по Bluetooth. */
#pragma once

#include <stdbool.h>

#include "noctis_core.h"

/** Запускает задачу наблюдения (раз в секунду): итог смены сети, потеря связи с сервером, долгое нажатие BOOT. */
void netwatch_start(void);

/** Команда сервера set_wifi: переход в другую сеть. false — уже идёт смена. Если за 45 с не удалось подключиться
 *  и связаться с сервером, возвращается прежняя сеть, а итог (wifi_result) уходит серверу уже оттуда. */
bool netwatch_wifi_change(const noctis_command_t* cmd);

/** Следующая загрузка — режим повторной настройки по Bluetooth (связь с сервером сохраняется). */
void netwatch_enter_reconfigure(const char* reason);

/** Реализуется в main.c: есть ли рабочее соединение с сервером. */
bool net_server_connected(void);

/** Кнопки: BOOT (GPIO0) и внешняя (GPIO11 — GND). Любая из них даёт то же действие. */
void netwatch_button_init(void);
bool netwatch_button_pressed(void);
