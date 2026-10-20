/** Вывод звука на динамик MAX98357A — реализуется в main.c, используется звонком будильника и музыкой. */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/** Тон freq_hz длительностью ms (блокирует). alarm=true: громкость не ниже 50 %, чтобы будильник нельзя было проспать
 *  из-за тихой настройки голоса; при громкости 0 («без звука») всё равно звонит. */
void audio_play_tone(int freq_hz, int ms, bool alarm);

/** PCM16 16 кГц моно музыки (усиление уже применено). Блокирует, пока данные не приняты I2S. */
void audio_write_music(const int16_t* samples, size_t count);

/** Динамик свободен: устройство слушает слово пробуждения (не пишет команду, не ждёт и не воспроизводит ответ). */
bool audio_is_idle(void);

/** Текущая громкость 0..100. */
int audio_get_volume(void);
