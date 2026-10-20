# Этап 2 — Прошивка: план

Названия: **Noctis** — платформа/продукт; **Nils (Нильс)** — слово пробуждения и имя ИИ-модели
ассистента. Ничего не переименовывается.

## 1. Цели

1. Прошивка больше не зависит от жёстко заданных Wi-Fi/IP: настройки хранятся в NVS, сопряжение — по BLE.
2. Устройство аутентифицируется на сервере (`device_id` + `token`), получает и сохраняет персонализацию
   (`config`: слово пробуждения, голос, характер, язык, громкость, часовой пояс).
3. Обновление по воздуху (OTA) с откатом и проверкой SHA-256; таблица разделов под два образа.
4. Экран SSD1306: состояние, код сопряжения, IP, версия.
5. Фундамент для этапов 3–5: хранилище настроек, протокол `config`/`device_info`/`command`,
   зарезервированные разделы под модель слова пробуждения (этап 4).
6. Всё, что можно, проверяется без железа: хост-тесты Си-логики, «виртуальная колонка» (Python) против
   настоящего сервера, сборка прошивки в PlatformIO.

Не входит в этап: смена самой модели слова пробуждения (этап 4), время/таймеры/будильники/музыка
(этап 5), приложение (этап 6). MQTT для умного дома — этап 6 (его использует приложение).

## 2. Архитектура

```
app_main
 ├─ settings   (NVS: Wi-Fi-флаг, server, device_id, token, wake_word, voice, character, language,
 │              volume, timezone, config_revision, pop)         ← чистая логика + NVS-обвязка
 ├─ provisioning (BLE, wifi_provisioning, security 1 + PoP; свой endpoint «noctis-config»
 │              принимает server / device_id / token; удержание BOOT 5 с = повторное сопряжение)
 ├─ net        (Wi-Fi STA + WebSocket с device_id/token, переподключение, device_info при старте)
 ├─ protocol   (разбор сообщений сервера: config / command; сборка device_info)  ← чистая логика
 ├─ ota        (command ota → HTTPS/HTTP-загрузка в неактивный слот, SHA-256, rollback, отметка valid)
 ├─ display    (SSD1306 через esp_lcd; экраны: pairing / connecting / idle / listening / error)
 └─ audio      (существующий пайплайн: INMP441 → esp-sr WakeNet+VAD → WS → MAX98357A), громкость из settings
```

Чистая логика (без ESP-IDF) лежит в `components/noctis_core/` и собирается на ПК для тестов:
валидация и нормализация настроек, разбор `config`, сравнение версий, проверка hex SHA-256,
сборка URI. Платформенные модули (`settings.c`, `provisioning.c`, `net.c`, `ota.c`, `display.c`)
тонко оборачивают её.

Протокол (расширяет прежний, обратно совместим):
- → `device_info {firmware, config_revision, free_heap, reset_reason}` сразу после подключения;
- ← `config {wake_word, voice_gender, character_id, language, volume, timezone, revision}`;
- ← `command {name: reboot | factory_reset | ota, ...}`; для `ota`: `version`, `url`, `sha256`, `size`;
- → `ota_status {version, state: started|ok|failed, error}`.

Разделы flash (16 MB): `nvs 24K · otadata 8K · phy 4K · ota_0 3M · ota_1 3M · model 8M (esp-sr) ·
wakeword 1M (этап 4) · storage ~0.9M (звуки сигналов)`.

## 3. Файлы

Новые: `firmware/components/noctis_core/{include/noctis_core.h, noctis_core.c}`,
`firmware/main/{settings,provisioning,net,ota,display}.{c,h}`, `firmware/tests/` (хост-тесты, CMake-free
скрипт), `server/tools/virtual_speaker.py`, `server/noctis_server/routers/device_api.py`,
`server/tests/test_firmware_integration.py`, `docs/stage2-firmware-report.md`.
Изменяются: `firmware/main/main.c` (подключение модулей, громкость, имя Noctis в логах),
`partitions.csv`, `sdkconfig.defaults` (NimBLE, OTA-rollback), `platformio.ini`, `flash_models.py`
(адрес раздела), `include/config.h.sample`, `main/CMakeLists.txt`, `idf_component.yml`;
сервер: `gateway.py` (`device_info`, `ota_status`), `routers/devices.py` (запуск OTA).

## 4. План тестирования

| Уровень | Что | Как |
|---|---|---|
| Хост-тесты Си | валидация настроек, разбор `config`, версии, SHA-256 hex, сборка URI | `zig cc` на Windows, `python firmware/tests/run_host_tests.py` |
| Сборка | прошивка компилируется (ESP-IDF 5.4) | `pio run` |
| Интеграция | виртуальная колонка ↔ настоящий сервер: аутентификация, config → «NVS», голосовой цикл, смена настроек из API, reboot, OTA со скачиванием и проверкой хэша, неверный хэш отклоняется | `pytest server/tests/test_firmware_integration.py` |
| Регресс | 29 тестов этапа 1 | `pytest` |
| Не проверяется (нужна плата) | BLE-сопряжение, I2S, WakeNet, SSD1306, реальный OTA | чек-лист в отчёте |

## 5. Критерии приёмки

1. `pio run` завершается успешно; размер приложения помещается в слот 3 MB.
2. Хост-тесты и `pytest` проходят полностью.
3. Без Wi-Fi в NVS прошивка запускает BLE-сопряжение и показывает код (PoP) на экране и в логе.
4. После сопряжения устройство подключается к серверу с токеном; неверный токен — отказ, прошивка
   не зацикливается (backoff).
5. Полученный `config` сохраняется в NVS, переживает перезагрузку, применяется (громкость сразу).
6. OTA: образ скачивается, SHA-256 проверяется до переключения слота; при ошибке остаётся старая версия;
   новая версия подтверждает себя после успешного подключения (иначе откат).
7. Прежний голосовой цикл не сломан (логика пайплайна не менялась, кроме громкости).
8. Документация: README прошивки, отчёт, чек-лист проверки на плате.
