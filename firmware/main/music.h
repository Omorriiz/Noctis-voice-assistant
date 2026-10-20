/** Музыкальный плеер платы: принимает PCM-поток сервера (music_*), буферизует в PSRAM и проигрывает,
 *  когда устройство свободно. Логика буфера и состояний — noctis_audio.c (хост-тесты). */
#pragma once

#include <stdbool.h>
#include <stddef.h>

void music_init(void);                                 // после speaker_init()
void music_handle(const char* json, size_t len);       // сообщения music_start/chunk/end/pause/resume/stop
void music_stop_local(void);                           // принудительно (начался диалог, зазвонил будильник)
bool music_active(void);                               // идёт трек
const char* music_title(void);                         // «исполнитель — название» для экрана
