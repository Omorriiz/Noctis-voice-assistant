"""Этап 8, приватность: выгрузка «моих данных», очистка истории, полное стирание при удалении учётной записи (по запросу пользователя)."""
import json

from noctis_server.config import settings
from noctis_server.db import SessionLocal
from noctis_server.models import Alarm, Device, HistoryEntry, MediaTrack, Timer, User, UserDevice, utcnow

from .conftest import register
from .helpers import open_ws, paired

PASSWORD = "correct-horse-1"


def headers(tokens):
    return {"Authorization": "Bearer " + tokens["access_token"]}


def fill(pid, texts=("привет", "который час")):
    """Две записи истории, таймер и будильник у колонки pid."""
    with SessionLocal() as db:
        d = db.query(Device).filter(Device.public_id == pid).one()
        for t in texts:
            db.add(HistoryEntry(device_id=d.id, user_text=t, assistant_text="ответ на " + t, language="ru"))
        db.add(Timer(device_id=d.id, label="чай", duration_sec=300, remaining_sec=300, state="running", ends_at=utcnow()))
        db.add(Alarm(device_id=d.id, label="подъём", hour=7, minute=30, weekdays=[0, 1, 2, 3, 4]))
        db.commit()


def counts():
    with SessionLocal() as db:
        return {m.__name__: db.query(m).count() for m in (User, Device, UserDevice, HistoryEntry, Timer, Alarm, MediaTrack)}


# ---------------------------------------------------------------- выгрузка
def test_export_contains_everything_and_no_secrets(client, auth):
    pid, token = paired(client, auth, "Кухня")
    fill(pid)
    client.patch(f"/api/v1/devices/{pid}/settings", json={"home_city": "Казань"}, headers=auth)
    r = client.get("/api/v1/me/export", headers=auth)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and ".json" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    data = json.loads(r.content)
    assert data["account"]["email"] == "user@example.com" and data["account"]["consent_at"] and data["settings"]["language"]
    dev = data["devices"][0]
    assert dev["id"] == pid and dev["name"] == "Кухня" and dev["settings"]["home_city"] == "Казань"
    assert [h["you_said"] for h in dev["history"]] == ["привет", "который час"] and dev["timers"][0]["label"] == "чай"
    assert dev["alarms"][0]["time"] == "07:30" and dev["alarms"][0]["weekdays"] == [0, 1, 2, 3, 4]
    assert data["sessions"] and set(data["sessions"][0]) == {"created_at", "expires_at", "revoked", "user_agent"}
    text = r.text
    with SessionLocal() as db:
        secrets_in_db = [db.query(User).one().password_hash, db.query(Device).one().token_hash, token]
    for secret in secrets_in_db:
        assert secret not in text                                              # ни хэша пароля, ни токена устройства, ни хэшей
    assert "scrypt$" not in text and "password_hash" not in text and "refresh_hash" not in text


def test_export_needs_login_and_never_mixes_users(client, auth):
    assert client.get("/api/v1/me/export").status_code == 401
    pid, _ = paired(client, auth, "Моя")
    fill(pid, ("секрет первого",))
    other = register(client, email="second@example.com", password="slate-orbit-77")
    pid2, _ = paired(client, headers(other), "Чужая")
    fill(pid2, ("секрет второго",))
    mine = client.get("/api/v1/me/export", headers=auth).text
    theirs = client.get("/api/v1/me/export", headers=headers(other)).text
    assert "секрет первого" in mine and "секрет второго" not in mine and "second@example.com" not in mine
    assert "секрет второго" in theirs and "секрет первого" not in theirs and "user@example.com" not in theirs


# ---------------------------------------------------------------- очистка истории
def test_clear_history_only_touches_own_devices(client, auth):
    pid, _ = paired(client, auth)
    fill(pid)
    other = register(client, email="second@example.com", password="slate-orbit-77")
    pid2, _ = paired(client, headers(other))
    fill(pid2)
    assert client.delete("/api/v1/me/history", headers=auth).json() == {"deleted": 2}
    assert client.get(f"/api/v1/devices/{pid}/history", headers=auth).json() == []
    assert len(client.get(f"/api/v1/devices/{pid2}/history", headers=headers(other)).json()) == 2
    assert client.delete("/api/v1/me/history", headers=auth).json() == {"deleted": 0}
    assert client.delete("/api/v1/me/history").status_code == 401


# ---------------------------------------------------------------- стирание
def test_account_deletion_erases_devices_history_timers_alarms_and_files(client, auth, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "music_dir", str(tmp_path / "music"))
    pid, _ = paired(client, auth)
    fill(pid)
    with SessionLocal() as db:
        uid = db.query(User).one().id
        db.add(MediaTrack(owner_id=uid, title="t", source="local", uri="a.mp3"))
        db.commit()
    mine = tmp_path / "music" / str(uid)
    mine.mkdir(parents=True)
    (mine / "a.mp3").write_bytes(b"x" * 10)
    assert client.request("DELETE", "/api/v1/me", json={"password": "wrong"}, headers=auth).status_code == 403
    assert counts()["Device"] == 1                                              # неверный пароль — ничего не удалено
    assert client.request("DELETE", "/api/v1/me", json={"password": PASSWORD}, headers=auth).status_code == 204
    assert counts() == {"User": 0, "Device": 0, "UserDevice": 0, "HistoryEntry": 0, "Timer": 0, "Alarm": 0, "MediaTrack": 0}
    assert not mine.exists()


def test_deleting_the_account_resets_an_online_speaker(client, auth):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        assert client.request("DELETE", "/api/v1/me", json={"password": PASSWORD}, headers=auth).status_code == 204
        assert ws.receive_json() == {"type": "command", "name": "factory_reset"}
    finally:
        ctx.__exit__(None, None, None)


def test_other_users_data_survives_and_shared_device_is_kept(client, auth):
    pid, _ = paired(client, auth, "Общая")
    fill(pid)
    other = register(client, email="second@example.com", password="slate-orbit-77")
    pid2, _ = paired(client, headers(other), "Его")
    with SessionLocal() as db:                                                  # вторая учётная запись — тоже владелец первой колонки
        dev = db.query(Device).filter(Device.public_id == pid).one()
        uid2 = db.query(User).filter(User.email == "second@example.com").one().id
        db.add(UserDevice(user_id=uid2, device_id=dev.id, is_owner=False))
        db.commit()
    assert client.request("DELETE", "/api/v1/me", json={"password": PASSWORD}, headers=auth).status_code == 204
    c = counts()
    assert c["User"] == 1 and c["Device"] == 2 and c["HistoryEntry"] == 2          # общая колонка и чужая остались
    assert {d["public_id"] for d in client.get("/api/v1/devices", headers=headers(other)).json()} == {pid, pid2}


def test_admin_deleting_a_user_erases_the_same_way(client, auth):
    from noctis_server.models import Role
    other = register(client, email="victim@example.com", password="slate-orbit-77")
    pid, _ = paired(client, headers(other))
    fill(pid)
    with SessionLocal() as db:
        admin = db.query(User).filter(User.email == "user@example.com").one()
        admin.role_id = db.query(Role).filter(Role.name == "admin").one().id
        victim_id = db.query(User).filter(User.email == "victim@example.com").one().id
        db.commit()
    tokens = client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD}).json()
    assert client.delete(f"/api/v1/admin/users/{victim_id}", headers=headers(tokens)).status_code == 204
    assert counts()["Device"] == 0 and counts()["HistoryEntry"] == 0 and counts()["Timer"] == 0 and counts()["Alarm"] == 0
