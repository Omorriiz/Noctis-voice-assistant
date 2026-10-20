"""Этап 8: анонимные устройства выключены по умолчанию, случайный ключ подписи, парольная политика, сброс и смена пароля."""
import logging
from datetime import timedelta

import pytest
from starlette.websockets import WebSocketDisconnect

from noctis_server import config
from noctis_server.config import Settings, resolve_secret, settings
from noctis_server.db import SessionLocal
from noctis_server.models import PasswordReset, utcnow
from noctis_server.password_policy import policy_error

from .conftest import register


# ---------------------------------------------------------------- №3: устройства без токена
def test_anonymous_devices_are_off_by_default():
    assert Settings.model_fields["allow_anonymous_devices"].default is False
    assert Settings.model_fields["expose_reset_token"].default is False


def test_device_without_token_is_rejected_by_default(client):
    settings.allow_anonymous_devices = False
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/noctis/v1/") as ws:
            ws.receive_json()


# ---------------------------------------------------------------- №4: ключ подписи
def test_explicit_long_key_is_used_as_is(tmp_path):
    s = Settings(secret_key="x" * 40)
    assert resolve_secret(s, tmp_path / "secret.key") == "x" * 40
    assert not (tmp_path / "secret.key").exists()


@pytest.mark.parametrize("weak", ["", "short", "dev-only-secret-change-me-0123456789abcdef", "change-me-to-a-long-random-string-xyz"])
def test_missing_short_or_placeholder_key_gets_a_random_one(tmp_path, weak):
    path = tmp_path / "data" / "secret.key"
    key = resolve_secret(Settings(secret_key=weak), path)
    assert len(key) == 64 and key != weak and "dev-only" not in key
    assert path.read_text() == key
    assert resolve_secret(Settings(secret_key=weak), path) == key                 # при следующем запуске тот же ключ (токены остаются верны)
    other = resolve_secret(Settings(secret_key=weak), tmp_path / "other.key")
    assert other != key                                                          # у другой установки — другой


def test_app_never_signs_with_the_old_public_key():
    assert "dev-only" not in settings.secret_key and len(settings.secret_key) >= 32
    assert config.SECRET_FILE.name == "secret.key"


def test_forged_token_with_the_old_public_key_is_rejected(client):
    import jwt
    forged = jwt.encode({"sub": "1", "role": "admin", "type": "access", "exp": 9999999999},
                        "dev-only-secret-change-me-0123456789abcdef", algorithm="HS256")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_server_refuses_to_start_with_a_short_key(assistant, monkeypatch):
    from fastapi.testclient import TestClient
    from noctis_server.main import create_app
    monkeypatch.setattr(settings, "secret_key", "short")
    with pytest.raises(RuntimeError):
        with TestClient(create_app(assistant)):
            pass


# ---------------------------------------------------------------- №9: пароли
@pytest.mark.parametrize("weak", ["password", "password1", "Password123", "12345678", "123456789012", "qwertyuiop", "Qwerty123", "aaaaaaaaaa",
                                  "abcdefgh", "11111111", "пароль123", "йцукенгшщзх", "1q2w3e4r", "iloveyou1", "noctis123", "abababab"])
def test_weak_passwords_are_rejected_at_registration(client, weak):
    r = client.post("/api/v1/auth/register", json={"email": "weak@example.com", "password": weak, "consent": True})
    assert r.status_code == 422, weak


def test_password_with_email_or_name_is_rejected(client):
    r = client.post("/api/v1/auth/register", json={"email": "alexandra@example.com", "password": "alexandra-2026", "consent": True})
    assert r.status_code == 422 and "email" in r.text
    r = client.post("/api/v1/auth/register", json={"email": "x@example.com", "password": "ivan-petrov-77", "display_name": "Ivan Petrov",
                                                   "consent": True})
    assert r.status_code == 422 and "name" in r.text


@pytest.mark.parametrize("good", ["slate-orbit-77", "purple monkey dishwasher", "Zr7#kLm2pQ", "correct-horse-1"])
def test_reasonable_passwords_are_accepted(client, good):
    assert client.post("/api/v1/auth/register", json={"email": "ok@example.com", "password": good, "consent": True}).status_code == 201


def test_policy_function_reasons():
    assert policy_error("slate-orbit-77", "me@example.com", "Me") is None
    assert "common" in policy_error("password1")
    assert "sequence" in policy_error("abcdefghij")
    assert "different" in policy_error("aabbaabb1")


def test_unknown_and_known_email_get_identical_reset_answers(client, monkeypatch, caplog):
    monkeypatch.setattr(settings, "expose_reset_token", False)
    register(client)
    with caplog.at_level(logging.DEBUG):
        known = client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})
        unknown = client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()
    assert "dev_token" not in known.json()
    with SessionLocal() as db:                                                  # токен выдан, но наружу (ответ, журнал) не вышел
        assert db.query(PasswordReset).count() == 1
    assert "dev password reset token" not in caplog.text.lower()


def test_reset_token_is_single_use_and_only_latest_works(client):
    register(client)
    first = client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"}).json()["dev_token"]
    second = client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"}).json()["dev_token"]
    assert client.post("/api/v1/auth/reset-password", json={"token": first, "new_password": "fresh-pass-phrase-1"}).status_code == 400
    assert client.post("/api/v1/auth/reset-password", json={"token": second, "new_password": "password1"}).status_code == 422   # слабый — отказ
    assert client.post("/api/v1/auth/reset-password", json={"token": second, "new_password": "fresh-pass-phrase-1"}).status_code == 204
    assert client.post("/api/v1/auth/reset-password", json={"token": second, "new_password": "another-pass-phrase-2"}).status_code == 400


def test_expired_reset_token_is_rejected(client):
    register(client)
    token = client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"}).json()["dev_token"]
    with SessionLocal() as db:
        db.query(PasswordReset).update({"expires_at": utcnow() - timedelta(minutes=1)})
        db.commit()
    assert client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "fresh-pass-phrase-1"}).status_code == 400
    assert settings.reset_token_minutes <= 60                                    # срок жизни короткий


def test_change_password_checks_policy_and_ends_sessions(client):
    tokens = register(client)
    auth = {"Authorization": "Bearer " + tokens["access_token"]}
    assert client.post("/api/v1/auth/change-password", json={"current_password": "correct-horse-1", "new_password": "password1"},
                       headers=auth).status_code == 422
    assert client.post("/api/v1/auth/change-password", json={"current_password": "wrong", "new_password": "fresh-pass-phrase-1"},
                       headers=auth).status_code == 403
    assert client.post("/api/v1/auth/change-password", json={"current_password": "correct-horse-1", "new_password": "fresh-pass-phrase-1"},
                       headers=auth).status_code == 204
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401     # сессии завершены
    assert client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "fresh-pass-phrase-1"}).status_code == 200
