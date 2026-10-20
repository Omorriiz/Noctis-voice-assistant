import json

from noctis_server.db import SessionLocal
from noctis_server.models import Device, HistoryEntry

from .conftest import make_admin, register


def hdr(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_user_settings_patch_and_validation(client, auth):
    r = client.patch("/api/v1/me/settings", json={"language": "de", "theme": "dark", "voice_gender": "female",
                                                  "character_id": "wise", "wake_word": "alexa"}, headers=auth)
    assert r.status_code == 200 and r.json()["language"] == "de" and r.json()["character_id"] == "wise"
    for bad in ({"language": "xx"}, {"theme": "pink"}, {"voice_gender": "robot"}, {"character_id": "evil"}):
        assert client.patch("/api/v1/me/settings", json=bad, headers=auth).status_code == 422


def test_device_lifecycle_inherits_user_settings(client, auth):
    client.patch("/api/v1/me/settings", json={"language": "fr", "voice_gender": "female"}, headers=auth)
    r = client.post("/api/v1/devices", json={"name": "Kitchen"}, headers=auth)
    assert r.status_code == 201
    dev = r.json()
    assert dev["device_token"] and dev["settings"]["language"] == "fr" and dev["settings"]["voice_gender"] == "female"
    pid = dev["public_id"]

    assert [d["public_id"] for d in client.get("/api/v1/devices", headers=auth).json()] == [pid]
    assert "device_token" not in client.get(f"/api/v1/devices/{pid}", headers=auth).json()

    r = client.patch(f"/api/v1/devices/{pid}/settings", json={"wake_word": "alexa", "volume": 40}, headers=auth)
    assert r.json()["revision"] == 2 and r.json()["wake_word"] == "alexa"
    assert client.patch(f"/api/v1/devices/{pid}/settings", json={"volume": 500}, headers=auth).status_code == 422

    assert client.delete(f"/api/v1/devices/{pid}", headers=auth).status_code == 204
    assert client.get(f"/api/v1/devices/{pid}", headers=auth).status_code == 404


def test_devices_are_isolated_between_users(client, auth):
    pid = client.post("/api/v1/devices", json={}, headers=auth).json()["public_id"]
    other = hdr(register(client, email="other@example.com"))
    assert client.get(f"/api/v1/devices/{pid}", headers=other).status_code == 404
    assert client.patch(f"/api/v1/devices/{pid}/settings", json={"volume": 1}, headers=other).status_code == 404
    assert client.delete(f"/api/v1/devices/{pid}", headers=other).status_code == 404
    assert client.get("/api/v1/devices", headers=other).json() == []


def test_reboot_offline_device(client, auth):
    pid = client.post("/api/v1/devices", json={}, headers=auth).json()["public_id"]
    assert client.post(f"/api/v1/devices/{pid}/reboot", headers=auth).status_code == 409


def test_history_clear_export_and_privacy(client, auth):
    pid = client.post("/api/v1/devices", json={}, headers=auth).json()["public_id"]
    with SessionLocal() as db:
        d = db.query(Device).one()
        db.add_all([HistoryEntry(device_id=d.id, user_text=f"q{i}", assistant_text=f"a{i}") for i in range(3)])
        db.commit()
    rows = client.get(f"/api/v1/devices/{pid}/history", headers=auth).json()
    assert [r["user_text"] for r in rows] == ["q2", "q1", "q0"]
    exported = client.get(f"/api/v1/devices/{pid}/history/export", headers=auth)
    assert len(json.loads(exported.text)) == 3 and "attachment" in exported.headers["content-disposition"]
    assert client.delete(f"/api/v1/devices/{pid}/history", headers=auth).status_code == 204
    assert client.get(f"/api/v1/devices/{pid}/history", headers=auth).json() == []


def test_ota_upload_requires_admin(client, auth):
    assert client.put("/api/v1/ota/1.0.0", content=b"fw", headers=auth).status_code == 403
    make_admin()
    login = client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "correct-horse-1"}).json()
    adm = hdr(login)
    assert client.put("/api/v1/ota/bad", content=b"fw", headers=adm).status_code == 422
    r = client.put("/api/v1/ota/1.0.0", content=b"firmware-bytes", headers=adm)
    assert r.status_code == 201 and r.json()["size_bytes"] == 14
    client.put("/api/v1/ota/1.1.0", content=b"newer", headers=adm)
    assert client.get("/api/v1/ota/latest", headers=auth).json()["version"] == "1.1.0"
    assert client.get("/api/v1/ota/1.0.0/download", headers=auth).content == b"firmware-bytes"
    assert client.put("/api/v1/ota/1.1.0", content=b"x", headers=adm).status_code == 409
