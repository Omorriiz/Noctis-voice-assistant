import os

# Настройки тестов задаются ДО импорта приложения
os.environ["NOCTIS_DATABASE_URL"] = "sqlite:///:memory:"
os.environ["NOCTIS_LOAD_MODELS"] = "false"
os.environ["NOCTIS_SEED_WAKE_MODELS"] = "false"
os.environ["NOCTIS_LOG_DIR"] = "logs_test"
os.environ["NOCTIS_SCHEDULER_ENABLED"] = "false"   # цикл таймеров в тестах запускается вручную (tick)
os.environ["NOCTIS_JAMENDO_CLIENT_ID"] = ""
os.environ["NOCTIS_RATE_LIMIT_AUTH_REQUESTS"] = "1000"
os.environ["NOCTIS_SECRET_KEY"] = "test-signing-key-0123456789-abcdefghijklmnop"   # не создавать data/secret.key при тестах
os.environ["NOCTIS_EXPOSE_RESET_TOKEN"] = "true"

import pytest
from fastapi.testclient import TestClient

from noctis_server import registry
from noctis_server.config import settings
from noctis_server.db import Base, SessionLocal, engine
from noctis_server.main import create_app
from noctis_server.ratelimit import limiter


class FakeAssistant:
    """Заглушка голосового конвейера: без GPU и моделей."""

    def __init__(self):
        self.calls = []
        self.stt_text = "расскажи что нибудь интересное"   # что «услышал» распознаватель; тесты команд меняют

    def transcribe(self, audio, device_settings=None):
        self.calls.append(("stt", len(audio)))
        return self.stt_text

    def ask_llm_stream(self, user_text, history, device_settings=None):
        self.calls.append(("llm", user_text, dict(device_settings or {})))
        yield "Сейчас вечер."
        yield "Хорошего дня."
        history.append({"role": "user", "content": user_text})

    def tts_pcm16(self, text, device_settings=None):
        self.calls.append(("tts", text))
        return b"\x01\x00" * 3000


@pytest.fixture(autouse=True)
def _ota_dir(tmp_path, monkeypatch):
    """Образы прошивок в тестах пишутся во временную папку, а не в data/ota."""
    from noctis_server.routers import device_api, ota
    monkeypatch.setattr(ota, "OTA_DIR", tmp_path / "ota")
    monkeypatch.setattr(device_api, "OTA_DIR", tmp_path / "ota")


@pytest.fixture(autouse=True)
def _wake_dir(tmp_path, monkeypatch):
    """Модели слов, которые создают тесты, пишутся во временную папку, а не в server/data/wakewords."""
    import noctis_server.config as cfg
    from noctis_server.routers import wakewords
    monkeypatch.setattr(wakewords, "WAKE_DIR", tmp_path / "wakewords")
    monkeypatch.setattr(cfg, "BASE_DIR", tmp_path / "base")      # seed_wake_models читает BASE_DIR при вызове


@pytest.fixture(autouse=True)
def _no_tts_pacing(monkeypatch):
    """В тестах голос уходит без опережающей паузы (иначе тесты ждали бы реальное время речи); сам Pacer проверяет test_pacing."""
    from noctis_server.voice import gateway
    monkeypatch.setattr(gateway, "TTS_LEAD_SEC", 1e6)


@pytest.fixture(autouse=True)
def _jamendo_fresh(monkeypatch):
    """Ответы Jamendo запоминаются на полчаса: в тестах кэш пуст, повторные пачки без пауз."""
    from noctis_server.media import jamendo
    jamendo._cache.clear()
    monkeypatch.setattr(jamendo, "ROUND_PAUSE_SEC", 0.0)


@pytest.fixture(autouse=True)
def _clean():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    limiter.reset()
    registry.online_devices.clear()
    registry.ota_status.clear()
    registry.ringing.clear()
    registry.players.clear()
    settings.allow_anonymous_devices = True
    settings.store_audio = False
    yield


@pytest.fixture
def assistant():
    return FakeAssistant()


@pytest.fixture
def client(assistant):
    with TestClient(create_app(assistant)) as c:
        yield c


def run_async(client, func, *args):
    """Выполняет корутину в цикле событий приложения (там живут соединения устройств)."""
    return client.portal.call(func, *args)


def register(client, email="user@example.com", password="correct-horse-1", **extra):
    body = {"email": email, "password": password, "consent": True, **extra}
    r = client.post("/api/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def auth(client):
    tokens = register(client)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def make_admin(email="user@example.com"):
    from sqlalchemy import select
    from noctis_server.models import Role, User
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        user.role_id = db.scalar(select(Role).where(Role.name == "admin")).id
        db.commit()
