/** Таймеры и будильники на плате: расписание хранится в NVS, срабатывание по времени, звонок мелодией,
 *  главный экран (часы, ближайший таймер/будильник, что играет). Логика — noctis_sched.c (хост-тесты). */
#pragma once

#include <stdbool.h>
#include <stddef.h>

/** После nvs_flash_init() и speaker_init(): загружает расписание из NVS, запускает задачи опроса и звонка. */
void schedule_init(void);

/** Сообщение сервера "schedule": новый снимок (сохраняется в NVS). */
void schedule_apply(const char* json, size_t len);

bool schedule_ringing(void);

/** Остановить звонок (кнопка BOOT, слово пробуждения, команда сервера, голосовое уведомление).
 *  notify_server — сообщить серверу "ring_stopped". */
void schedule_stop_ring(bool notify_server);
