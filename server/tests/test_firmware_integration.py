"""Виртуальная колонка (с НАСТОЯЩЕЙ Си-логикой прошивки) против настоящего сервера."""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.virtual_speaker import (CMD_FACTORY_RESET, CMD_NONE, CMD_REBOOT, Core, VirtualSpeaker,  # noqa: E402
                                   build_core_library)

from .conftest import make_admin, register  # noqa: E402


@pytest.fixture(scope="session")
def core(tmp_path_factory):
    try:
        lib = build_core_library(tmp_path_factory.mktemp("core_lib"))
    except Exception as e:  # нет компилятора — тесты пропускаются, а не падают
        pytest.skip(f"не удалось собрать noctis_core: {e}")
    return Core(lib)


def hdr(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def paired(client, auth, core, tmp_path):
    """Устройство создано на сервере и «сопряжено» (данные ушли в виртуальную NVS)."""
    d = client.post("/api/v1/devices", json={"name": "Kitchen"}, headers=auth).json()
    speaker = VirtualSpeaker(core, tmp_path / "nvs.json")
    assert speaker.pair({"server": "testserver", "tls": False, "device_id": d["public_id"], "token": d["device_token"]})
    return d["public_id"], speaker


def connect(client, speaker):
    return client.websocket_connect("/noctis/v1/", headers=speaker.headers())


def http_get(client):
    return lambda url, headers: client.get(url.split("://testserver", 1)[1], headers=headers)


def test_pairing_payload_validation(core, tmp_path):
    sp = VirtualSpeaker(core, tmp_path / "n.json")
    assert not sp.pair({"server": "h:1/evil?x=1", "device_id": "a", "token": "b"})
    assert not sp.pair({"server": "h", "device_id": "a&b", "token": "t"})
    assert not sp.pair({"server": "h", "device_id": "a"})
    assert sp.link_data is None


def test_headers_auth_and_config_persisted_across_reboot(client, auth, paired):
    pid, sp = paired
    client.patch(f"/api/v1/devices/{pid}/settings", json={"voice_gender": "female", "character_id": "wise",
                                                          "language": "de", "volume": 35, "wake_word": "alexa"},
                 headers=auth)
    with connect(client, sp) as ws:
        sp.handle(ws, ws.receive_json())          # hello
        sp.handle(ws, ws.receive_json())          # config
        sp.send_device_info(ws)
    s = sp.settings.as_dict()
    assert (s["voice_gender"], s["character_id"], s["language"], s["volume"], s["wake_word"]) == \
           ("female", "wise", "de", 35, "alexa")
    assert sp.volume_applied == 35

    sp.boot()                                      # перезагрузка: настройки читаются из «NVS»
    assert sp.settings.as_dict()["character_id"] == "wise"
    assert sp.boot_count == 3   # 1 — включение, 2 — перезапуск из-за смены слова на «computer» (этап 4), 3 — ручная
    # сервер увидел версию прошивки из device_info
    assert client.get(f"/api/v1/devices/{pid}", headers=auth).json()["firmware_version"] == "0.4.0"


def test_wrong_token_is_rejected(client, paired):
    from starlette.websockets import WebSocketDisconnect
    pid, sp = paired
    bad = {"X-Device-Id": pid, "X-Device-Token": "wrong"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/noctis/v1/", headers=bad) as ws:
            ws.receive_json()


def test_voice_cycle_and_live_setting_push(client, auth, paired, assistant):
    pid, sp = paired
    with connect(client, sp) as ws:
        sp.handle(ws, ws.receive_json()); sp.handle(ws, ws.receive_json())
        sp.speak_command(ws, b"\x03\x00" * 16000)
        reply = sp.listen_for_reply(ws)
        assert reply["chunks"] > 0 and reply["texts"][-1] == "Сейчас вечер. Хорошего дня."
        assert sp.played_bytes > 0

        # смена настроек в приложении доходит до колонки сразу и сохраняется
        client.patch(f"/api/v1/devices/{pid}/settings", json={"volume": 20, "character_id": "calm"}, headers=auth)
        pushed = ws.receive_json()
        sp.handle(ws, pushed)
        assert sp.volume_applied == 20 and sp.settings.as_dict()["character_id"] == "calm"


def test_reboot_and_factory_reset_commands(client, auth, paired):
    pid, sp = paired
    with connect(client, sp) as ws:
        sp.handle(ws, ws.receive_json()); sp.handle(ws, ws.receive_json())
        client.post(f"/api/v1/devices/{pid}/reboot", headers=auth)
        sp.handle(ws, ws.receive_json())
        assert sp.last_command == CMD_REBOOT and sp.boot_count == 2
        sp.handle(ws, {"type": "command", "name": "factory_reset"})
        assert sp.last_command == CMD_FACTORY_RESET and sp.link_data is None
        sp.handle(ws, {"type": "command", "name": "format_disk"})
        assert sp.last_command == CMD_NONE


def make_firmware(client, version, payload=b"FIRMWARE-IMAGE" * 100):
    make_admin()
    adm = hdr(client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "correct-horse-1"}).json())
    assert client.put(f"/api/v1/ota/{version}", content=payload, headers=adm).status_code == 201
    return payload


def run_ota(client, auth, pid, sp, version):
    with connect(client, sp) as ws:
        sp.handle(ws, ws.receive_json()); sp.handle(ws, ws.receive_json())
        r = client.post(f"/api/v1/devices/{pid}/ota", json={"version": version}, headers=auth)
        assert r.status_code == 202, r.text
        sp.handle(ws, ws.receive_json(), http_get(client))
        # прошивка отчиталась серверу — дочитываем её статусы
        import time
        for _ in range(50):
            state = client.get(f"/api/v1/devices/{pid}/ota", headers=auth).json()
            if state["state"] in ("ok", "failed"):
                return state
            time.sleep(0.05)
        return state


def test_ota_success_switches_slot_and_reports(client, auth, paired):
    pid, sp = paired
    make_firmware(client, "0.5.0")
    state = run_ota(client, auth, pid, sp, "0.5.0")
    assert state["state"] == "ok", (state, sp.ota_log)
    assert sp.firmware == "0.5.0" and sp.active_slot == "ota_1"


def test_ota_hash_mismatch_keeps_old_firmware(client, auth, paired):
    pid, sp = paired
    make_firmware(client, "0.5.0")
    # сервер «испортил» образ после публикации: хэш в БД не совпадает с файлом
    from noctis_server.routers.ota import OTA_DIR
    (OTA_DIR / "noctis-0.5.0.bin").write_bytes(b"corrupted-image")
    state = run_ota(client, auth, pid, sp, "0.5.0")
    assert state["state"] == "failed" and "sha256" in state["error"] or "size" in state["error"]
    assert sp.firmware == "0.4.0" and sp.active_slot == "ota_0"


def test_ota_refuses_downgrade_and_equal(client, auth, paired):
    pid, sp = paired
    make_firmware(client, "0.4.0")
    state = run_ota(client, auth, pid, sp, "0.4.0")
    assert state["state"] == "failed" and state["error"] == "not newer" and sp.firmware == "0.4.0"


def test_ota_requires_known_version_and_online_device(client, auth, paired):
    pid, sp = paired
    assert client.post(f"/api/v1/devices/{pid}/ota", json={"version": "9.9.9"}, headers=auth).status_code == 404
    make_firmware(client, "0.5.0")
    assert client.post(f"/api/v1/devices/{pid}/ota", json={"version": "0.5.0"}, headers=auth).status_code == 409
    assert client.post(f"/api/v1/devices/{pid}/ota", json={"version": "bad"}, headers=auth).status_code == 422


def test_device_download_endpoint_needs_device_credentials(client, auth, paired):
    pid, sp = paired
    make_firmware(client, "0.5.0")
    assert client.get("/api/v1/device/ota/0.5.0").status_code == 401
    assert client.get("/api/v1/device/ota/0.5.0", headers={"X-Device-Id": pid, "X-Device-Token": "x"}).status_code == 401
    r = client.get("/api/v1/device/ota/0.5.0", headers=sp.headers())
    assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest()
    # пользовательский токен не заменяет токен устройства
    assert client.get("/api/v1/device/ota/0.5.0", headers=auth).status_code == 401
    assert client.get("/api/v1/device/config", headers=sp.headers()).json()["wake_word"] == "nils"


def test_other_users_device_cannot_receive_ota_command(client, auth, paired):
    pid, sp = paired
    make_firmware(client, "0.5.0")
    other = hdr(register(client, email="other@example.com"))
    with connect(client, sp) as ws:
        ws.receive_json(); ws.receive_json()
        assert client.post(f"/api/v1/devices/{pid}/ota", json={"version": "0.5.0"}, headers=other).status_code == 404
