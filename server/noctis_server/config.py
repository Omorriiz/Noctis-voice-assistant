"""Настройки сервера Noctis. Читаются из переменных окружения NOCTIS_* и файла .env."""
import logging
import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOCTIS_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'noctis.db').as_posix()}"
    secret_key: str = ""   # пусто — случайный ключ создаётся при первом запуске и хранится в data/secret.key (этап 8)
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]
    serve_site: bool = True                # отдавать сайт (папка site/) самим сервером: сайт и API на одном адресе
    site_dir: str = str(BASE_DIR.parent / "site")

    # Токены
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    reset_token_minutes: int = 30

    # Ограничение частоты запросов (на IP): запросов за окно
    rate_limit_requests: int = 120
    rate_limit_auth_requests: int = 10
    rate_limit_window_sec: int = 60

    # Устройства
    allow_anonymous_devices: bool = False  # устройство без токена: только для очень старой прошивки; включать явно (этап 8)
    expose_reset_token: bool = False       # отдавать токен сброса пароля в ответе и логе (только для разработки: писем нет)
    ws_path: str = "/noctis/v1/"
    ws_legacy_path: str = "/nils/v1/"      # старый путь, пока прошивка не обновлена

    # Голосовой конвейер
    store_audio: bool = False              # не хранить записи голоса (приватность, 152-ФЗ)
    load_models: bool = True               # в тестах выключается
    seed_wake_models: bool = True          # импортировать готовые модели слов при старте
    wake_models_dir: str = str(BASE_DIR.parent / "wakeword" / "models")  # откуда (*.nwwm)
    whisper_model_dir: str = str(BASE_DIR / "models" / "faster-whisper")
    whisper_device: str = "cuda"           # cuda | cpu
    # int8_float16 вдвое меньше float16 по видеопамяти: на ноутбучной GPU 8 ГБ Whisper и Vikhr-12B иначе вытесняют друг друга
    whisper_compute: str = "int8_float16"  # float16 | int8_float16 | int8 (для cpu)
    whisper_beam: int = 5                  # 1 — быстрее, 5 — точнее
    ollama_model: str = "OxW/Vikhr-Nemo-12B-Instruct-R-21-09-24:q4_K_M"
    output_dir: str = str(BASE_DIR / "data" / "output")
    log_dir: str = str(BASE_DIR / "logs")

    # Музыка (этап 5): собственная библиотека, Jamendo API, интернет-радио — других источников нет
    music_dir: str = str(BASE_DIR / "data" / "music")   # <music_dir>/<id пользователя>/*.mp3|flac|ogg|wav
    music_upload_max_mb: int = 60
    jamendo_client_id: str = ""            # свой client_id с developer.jamendo.com; без него Jamendo выключен
    jamendo_api_url: str = "https://api.jamendo.com/v3.0"
    radio_stations_file: str = str(BASE_DIR / "data" / "radio_stations.json")  # необязательный список [{"name","url"}]
    scheduler_enabled: bool = True         # цикл таймеров/будильников (в тестах включается явно)
    test_console: bool = True              # страница /console для проверки колонки; в продакшне выключить

    @property
    def is_production(self) -> bool:
        return self.env == "production"


SECRET_FILE = BASE_DIR / "data" / "secret.key"
_PLACEHOLDERS = ("dev-only", "change-me", "changeme")
MIN_SECRET_LEN = 32


def resolve_secret(s: Settings, path: Path | None = None) -> str:
    """Подпись токенов: свой ключ из NOCTIS_SECRET_KEY (если он достаточно длинный и не образец из примера), иначе случайный,
    созданный при первом запуске и сохранённый в data/secret.key (в git не попадает). Старый публичный ключ из кода не используется."""
    key = (s.secret_key or "").strip()
    if len(key) >= MIN_SECRET_LEN and not any(p in key.lower() for p in _PLACEHOLDERS):
        return key
    path = path or SECRET_FILE
    try:
        saved = path.read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if len(saved) < MIN_SECRET_LEN:
        saved = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(saved, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        logging.getLogger("noctis").info("Создан случайный ключ подписи токенов: %s", path)
    return saved


def get_settings() -> Settings:
    s = Settings()
    s.secret_key = resolve_secret(s)
    return s


settings = get_settings()
