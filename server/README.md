# Noctis — сервер

Сервер умной колонки: принимает речь от колонки по WebSocket, распознаёт её, получает ответ языковой модели, озвучивает и отправляет обратно.
Кроме того, он хранит учётные записи и настройки, считает таймеры и будильники, ищет музыку, говорит погоду и раздаёт сайт из папки `../site`.

Стек: Python 3.12, FastAPI, SQLAlchemy + Alembic, SQLite (отдельный сервер БД не нужен), faster-whisper, Ollama (Vikhr-Nemo-12B), Silero TTS.

## Установка

```powershell
cd server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt          # API, база данных, тесты — этого достаточно, чтобы запустить тесты
copy .env.example .env                   # необязательно: значения по умолчанию рабочие
```

Голосовая часть (нужна видеокарта NVIDIA; для RTX 50xx — PyTorch с CUDA 12.8 и новее, ставьте его до остального):

```powershell
pip install -r requirements-ai.txt
ollama pull OxW/Vikhr-Nemo-12B-Instruct-R-21-09-24:q4_K_M
```

Модель распознавания речи (faster-whisper large-v3) положите в `models\faster-whisper-large-v3` либо укажите другой путь в `NOCTIS_WHISPER_MODEL_DIR`.

## Запуск

```powershell
python -m noctis_server
```

При старте сами применяются миграции базы. Сервер слушает порт 8000 на всех адресах (на другом компьютере откройте этот порт в брандмауэре).

| Адрес | Что там |
|---|---|
| `/` | сайт и веб-кабинет (из `../site`; отключается `NOCTIS_SERVE_SITE=false`) |
| `/docs` | Swagger — описание REST API |
| `/api` | статус сервера |
| `/noctis/v1/` | WebSocket для колонки |
| `/console` | страница проверки колонки без приложения (для владельца; в рабочей эксплуатации `NOCTIS_TEST_CONSOLE=false`) |

### Администратор

```powershell
python tools\create_admin.py --email you@example.com
```

Пароль запрашивается в терминале. Страница администратора открывается на сайте по **Alt+X**.

## Настройки

Читаются из переменных окружения `NOCTIS_*` и файла `.env` (все с примерами — в `.env.example`).

| Переменная | Назначение |
|---|---|
| `NOCTIS_DATABASE_URL` | база данных (по умолчанию `sqlite:///./data/noctis.db`) |
| `NOCTIS_SECRET_KEY` | ключ подписи токенов; если не задан, создаётся случайный в `data/secret.key` (**сделайте его копию**) |
| `NOCTIS_CORS_ORIGINS` | адреса сайтов, которым разрешено обращаться к API (нужно, только если сайт открыт не с этого сервера) |
| `NOCTIS_ALLOW_ANONYMOUS_DEVICES` | колонки без токена (по умолчанию выключено) |
| `NOCTIS_WHISPER_MODEL_DIR`, `NOCTIS_WHISPER_COMPUTE` | модель распознавания речи и точность вычислений |
| `NOCTIS_OLLAMA_MODEL` | языковая модель |
| `NOCTIS_MUSIC_DIR` | папка личной музыки: `<папка>/<id пользователя>/*.mp3\|flac\|ogg\|wav` (или загрузка через приложение) |
| `NOCTIS_JAMENDO_CLIENT_ID` | свой `client_id` с developer.jamendo.com (бесплатно); пусто — Jamendo выключен |
| `NOCTIS_RADIO_STATIONS_FILE` | свой список радиостанций, JSON `[{"name": "...", "url": "http://.../stream"}]` |
| `NOCTIS_STORE_AUDIO` | сохранять записи команд для отладки (по умолчанию нет) |

## Что умеет ассистент

Голосовые команды на пяти языках: «который час», «поставь таймер на пять минут», «разбуди меня в 7:30 по будням», «включи джаз», «пауза», «дальше»,
«громкость на сорок», «какая погода в Казани». Погода — на сегодня, источник Open-Meteo; город по умолчанию берётся из поля «Домашний город» в настройках колонки.
Слова пробуждения для пользователей — «Нильс» и «Alexa». Музыка декодируется Python-библиотекой `miniaudio`, ffmpeg не нужен.
Подробнее: [docs/weather-report.md](../docs/weather-report.md), [docs/jamendo-report.md](../docs/jamendo-report.md).

## Устройство

| Путь | Назначение |
|---|---|
| `noctis_server/main.py` | приложение FastAPI, подключение маршрутов, сайта и middleware безопасности |
| `noctis_server/routers/` | REST API: вход и учётные записи, колонки, настройки, расписание, музыка, администратор, обновления по воздуху, модели слов |
| `noctis_server/voice/` | WebSocket-шлюз колонки, конвейер речь → текст → ответ → голос, ассистент |
| `noctis_server/intents.py`, `skills.py`, `weather.py` | разбор команд и их выполнение |
| `noctis_server/media/` | своя библиотека, Jamendo, радио, декодирование и подача звука |
| `noctis_server/scheduler.py` | таймеры и будильники |
| `noctis_server/security.py`, `password_policy.py`, `security_headers.py`, `logredact.py`, `privacy.py` | пароли и токены, политика паролей, заголовки безопасности, скрытие секретов в журналах, выгрузка и удаление данных |
| `migrations/` | миграции Alembic |
| `tools/` | `create_admin.py` (администратор), `pair_device.py` (сопряжение с компьютера), `virtual_speaker.py` (имитация колонки для проверки), `board_log.py` (журнал платы), `scan_secrets.py` (поиск секретов) |
| `tests/` | тесты сервера |

## Тесты и обслуживание

```powershell
pytest                                   # видеокарта и плата не нужны
python -m noctis_server.backup           # резервная копия базы
python tools\scan_secrets.py             # поиск секретов в git, архивах и журналах
```

Файлы, которые нужно беречь: `data\noctis.db` (база), `data\secret.key` (ключ подписи), `.env`.
