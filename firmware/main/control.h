/** Управляющие сообщения сервера: config (персонализация) и command (reboot / factory_reset / ota). */
#pragma once

#include <stdbool.h>
#include <stddef.h>

/** Регистрирует функцию отправки текстового сообщения на сервер (WebSocket). */
void control_init(bool (*send)(const char* text, int len));

/** Отправка сообщения серверу; false, если нет соединения. */
bool control_send(const char* json);

/** Обрабатывает "config" / "command". Вызывается из обработчика WebSocket. */
void control_handle(const char* json, size_t len);

/** Отправляет device_info (после подключения). */
void control_send_device_info(void);

/** Реализуется в main.c: громкость воспроизведения 0..100. */
void audio_set_volume(int volume);

/** Реализуется в main.c: SHA-256 модели microWakeWord, записанной в разделе wakeword ("" — нет). */
const char* audio_installed_wake_sha(void);

/** Перезагрузка через 1,5 с (смена слова пробуждения / движка). */
void control_schedule_restart(void);
