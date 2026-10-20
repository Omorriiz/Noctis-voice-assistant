/** Экран SSD1306 128x64 (I2C). Если экрана нет — все вызовы безопасно ничего не делают. */
#pragma once

#include <stdbool.h>

bool display_init(void);
void display_status(const char* title, const char* line1, const char* line2);
void display_pairing(const char* ble_name, const char* pop);
void display_error(const char* title, const char* detail);
void display_idle(const char* wake_word, const char* ip, const char* fw);

/** Главный экран: время крупно + три строки (слово пробуждения, таймер, будильник/музыка). Не затирает
 *  сообщения display_status/pairing/error в течение нескольких секунд после их показа. */
void display_home(const char* hhmm, const char* line1, const char* line2, const char* line3);
