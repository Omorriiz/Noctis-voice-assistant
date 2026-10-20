from noctis_server.config import settings
from noctis_server.ratelimit import limiter

from .conftest import register


def test_register_requires_consent(client):
    r = client.post("/api/v1/auth/register", json={"email": "a@b.co", "password": "longenough-pass-1", "consent": False})
    assert r.status_code == 422


def test_register_login_me(client):
    register(client, display_name="Ann")
    r = client.post("/api/v1/auth/login", json={"email": "USER@example.com", "password": "correct-horse-1"})
    assert r.status_code == 200
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["email"] == "user@example.com" and me["display_name"] == "Ann"
    # значения по умолчанию из ТЗ
    assert me["settings"]["voice_gender"] == "male"
    assert me["settings"]["character_id"] == "friendly"
    assert me["settings"]["wake_word"] == "nils"


def test_duplicate_email_and_weak_password(client):
    register(client)
    r = client.post("/api/v1/auth/register", json={"email": "user@example.com", "password": "correct-horse-1", "consent": True})
    assert r.status_code == 409
    r = client.post("/api/v1/auth/register", json={"email": "x@example.com", "password": "short", "consent": True})
    assert r.status_code == 422


def test_wrong_password_and_unknown_user(client):
    register(client)
    assert client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "nope-nope-1"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "who@example.com", "password": "nope-nope-1"}).status_code == 401


def test_password_not_stored_in_clear():
    from noctis_server.security import hash_password, verify_password
    h = hash_password("secret-pass-1")
    assert "secret-pass-1" not in h and verify_password("secret-pass-1", h)
    assert not verify_password("other", h)


def test_protected_routes_need_token(client):
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_refresh_rotation_and_reuse_detection(client):
    tokens = register(client)
    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r1.status_code == 200
    new = r1.json()
    # повторное использование старого токена = кража: все сессии отзываются
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": new["refresh_token"]}).status_code == 401


def test_logout_revokes_refresh(client):
    tokens = register(client)
    assert client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_password_reset_flow(client):
    tokens = register(client)
    r = client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})
    assert r.status_code == 202
    token = r.json()["dev_token"]
    # неизвестный e-mail отвечает так же, но без токена
    r2 = client.post("/api/v1/auth/forgot-password", json={"email": "ghost@example.com"})
    assert r2.status_code == 202 and "dev_token" not in r2.json()

    assert client.post("/api/v1/auth/reset-password", json={"token": "bad", "new_password": "brand-new-pass-1"}).status_code == 400
    assert client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "brand-new-pass-1"}).status_code == 204
    # токен одноразовый, старые сессии завершены, новый пароль работает
    assert client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "another-pass-12"}).status_code == 400
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "brand-new-pass-1"}).status_code == 200


def test_change_password(client, auth):
    body = {"current_password": "wrong", "new_password": "brand-new-pass-1"}
    assert client.post("/api/v1/auth/change-password", json=body, headers=auth).status_code == 403
    body["current_password"] = "correct-horse-1"
    assert client.post("/api/v1/auth/change-password", json=body, headers=auth).status_code == 204


def test_auth_rate_limit(client):
    settings.rate_limit_auth_requests = 3
    try:
        limiter.reset()
        codes = [client.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "x"}).status_code for _ in range(5)]
        assert codes[:3] == [401, 401, 401] and codes[3] == 429
    finally:
        settings.rate_limit_auth_requests = 1000


def test_sql_injection_attempt_is_inert(client):
    register(client)
    r = client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "' OR '1'='1"})
    assert r.status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "x@example.com' OR '1'='1", "password": "x"})
    assert r.status_code == 422  # невалидный e-mail


def test_delete_account_removes_data(client, auth):
    assert client.post("/api/v1/devices", json={}, headers=auth).status_code == 201
    assert client.request("DELETE", "/api/v1/me", json={"password": "wrong"}, headers=auth).status_code == 403
    assert client.request("DELETE", "/api/v1/me", json={"password": "correct-horse-1"}, headers=auth).status_code == 204
    assert client.get("/api/v1/me", headers=auth).status_code == 401
