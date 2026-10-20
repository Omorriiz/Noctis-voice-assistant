"""Административный API: доступ только у роли admin, просмотр, действия, журнал."""
import importlib.util
import sys
from pathlib import Path

from sqlalchemy import select

from noctis_server.db import SessionLocal
from noctis_server.models import AuditLog, User
from noctis_server.services import get_role
from tests.helpers import paired


def register(client, email, name="Имя"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "slate-orbit-77", "display_name": name,
                                                   "consent": True, "language": "ru"})
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_admin(client, email="boss@example.com"):
    register(client, email, "Boss")
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email))
        u.role_id = get_role(db, "admin").id
        db.commit()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "slate-orbit-77"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def uid_of(client, admin, email):
    return next(r["id"] for r in client.get("/api/v1/admin/users", headers=admin).json() if r["email"] == email)


def test_admin_endpoints_require_admin(client):
    user = register(client, "u@example.com")
    for url in ("/api/v1/admin/stats", "/api/v1/admin/users", "/api/v1/admin/audit"):
        assert client.get(url).status_code == 401
        assert client.get(url, headers=user).status_code == 403


def test_list_and_detail_never_expose_password(client):
    admin = make_admin(client)
    user = register(client, "alice@example.com", "Alice")
    paired(client, user, "Кухня")
    rows = client.get("/api/v1/admin/users", headers=admin).json()
    alice = next(r for r in rows if r["email"] == "alice@example.com")
    assert alice["display_name"] == "Alice" and alice["devices"] == 1 and alice["role"] == "user"
    assert not any("password" in k for k in alice)
    detail = client.get(f"/api/v1/admin/users/{alice['id']}", headers=admin).json()
    assert detail["speakers"][0]["name"] == "Кухня"
    assert "password" not in str(detail).lower()
    assert client.get("/api/v1/admin/users?q=ALI", headers=admin).json()[0]["email"] == "alice@example.com"
    assert client.get("/api/v1/admin/users?q=nobody", headers=admin).json() == []
    stats = client.get("/api/v1/admin/stats", headers=admin).json()
    assert stats["users"] == 2 and stats["speakers"] == 1


def test_disable_blocks_login_and_enable_restores(client):
    admin = make_admin(client)
    register(client, "bob@example.com")
    uid = uid_of(client, admin, "bob@example.com")
    assert client.post(f"/api/v1/admin/users/{uid}/disable", headers=admin).status_code == 204
    r = client.post("/api/v1/auth/login", json={"email": "bob@example.com", "password": "slate-orbit-77"})
    assert r.status_code == 401
    assert client.post(f"/api/v1/admin/users/{uid}/enable", headers=admin).status_code == 204
    r = client.post("/api/v1/auth/login", json={"email": "bob@example.com", "password": "slate-orbit-77"})
    assert r.status_code == 200


def test_reset_password_gives_temp_password(client):
    admin = make_admin(client)
    register(client, "carol@example.com")
    uid = uid_of(client, admin, "carol@example.com")
    temp = client.post(f"/api/v1/admin/users/{uid}/reset-password", headers=admin).json()["temporary_password"]
    assert len(temp) >= 10
    assert client.post("/api/v1/auth/login", json={"email": "carol@example.com", "password": "slate-orbit-77"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "carol@example.com", "password": temp}).status_code == 200


def test_delete_user_and_device(client):
    admin = make_admin(client)
    user = register(client, "dave@example.com")
    pid, _ = paired(client, user)
    uid = uid_of(client, admin, "dave@example.com")
    assert client.delete(f"/api/v1/admin/devices/{pid}", headers=admin).status_code == 204
    assert client.delete("/api/v1/admin/devices/nope", headers=admin).status_code == 404
    assert client.delete(f"/api/v1/admin/users/{uid}", headers=admin).status_code == 204
    assert client.get(f"/api/v1/admin/users/{uid}", headers=admin).status_code == 404


def test_cannot_touch_admins_or_self(client):
    admin = make_admin(client)
    make_admin(client, "second@example.com")
    for email in ("boss@example.com", "second@example.com"):
        uid = uid_of(client, admin, email)
        assert client.post(f"/api/v1/admin/users/{uid}/disable", headers=admin).status_code == 403
        assert client.delete(f"/api/v1/admin/users/{uid}", headers=admin).status_code == 403
        assert client.post(f"/api/v1/admin/users/{uid}/reset-password", headers=admin).status_code == 403


def test_actions_are_audited(client):
    admin = make_admin(client)
    register(client, "erin@example.com")
    uid = uid_of(client, admin, "erin@example.com")
    client.post(f"/api/v1/admin/users/{uid}/disable", headers=admin)
    client.post(f"/api/v1/admin/users/{uid}/reset-password", headers=admin)
    log = client.get("/api/v1/admin/audit", headers=admin).json()
    actions = [(r["action"], r["target"]) for r in log]
    assert ("user_disable", "erin@example.com") in actions and ("user_reset_password", "erin@example.com") in actions
    assert any(a == "login" for a, _ in actions)          # вход администратора тоже записан
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog.id)) is not None


def test_last_login_recorded(client):
    admin = make_admin(client)
    register(client, "fred@example.com")
    client.post("/api/v1/auth/login", json={"email": "fred@example.com", "password": "slate-orbit-77"})
    row = next(r for r in client.get("/api/v1/admin/users", headers=admin).json() if r["email"] == "fred@example.com")
    assert row["last_login_at"] is not None


def test_create_admin_tool(monkeypatch):
    """Команда создаёт администратора с введённым паролем и умеет снимать права."""
    monkeypatch.setenv("NOCTIS_ADMIN_PASSWORD", "very-long-password")
    spec = importlib.util.spec_from_file_location("create_admin", Path(__file__).resolve().parents[1] / "tools" / "create_admin.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "migrate", lambda: None)             # схема в тестах уже создана
    monkeypatch.setattr(sys, "argv", ["create_admin.py", "--email", "Root@Example.com"])
    assert mod.main() == 0
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == "root@example.com")).role.name == "admin"
    monkeypatch.setattr(sys, "argv", ["create_admin.py", "--email", "root@example.com", "--demote"])
    assert mod.main() == 0
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == "root@example.com")).role.name == "user"
