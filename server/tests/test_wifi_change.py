"""Смена сети Wi-Fi без сброса: сервер + виртуальная колонка с НАСТОЯЩЕЙ Си-логикой прошивки (безопасный переход с откатом,
повторная настройка по Bluetooth с сохранением личности платы)."""
import logging
import time

from noctis_server import registry

from .test_firmware_integration import connect, core, hdr, paired  # noqa: F401  (фикстуры)

SECRET = "super-secret-pass"


def wait_state(client, auth, pid, want, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = client.get(f"/api/v1/devices/{pid}/wifi", headers=auth).json()
        if st["state"] == want:
            return st
        time.sleep(0.02)
    raise AssertionError(f"ожидали {want}, сейчас {st}")


def online(client, sp):
    """Подключает колонку и проходит приветствие, как прошивка."""
    ws_cm = connect(client, sp)
    ws = ws_cm.__enter__()
    sp.handle(ws, ws.receive_json())     # hello
    sp.handle(ws, ws.receive_json())     # config
    sp.send_device_info(ws)
    for _ in range(2):                   # в ответ сервер шлёт time_sync и schedule
        sp.handle(ws, ws.receive_json())
    return ws_cm, ws


def test_api_validation_and_offline(client, auth, paired):
    pid, _sp = paired
    url = f"/api/v1/devices/{pid}/wifi"
    assert client.get(url, headers=auth).json()["state"] == "none"
    assert client.post(url, json={"ssid": "Home", "password": SECRET}, headers=auth).status_code == 409   # колонка не на связи
    for bad in ({"ssid": ""}, {"ssid": "x" * 33}, {"ssid": "ok", "password": "short"}, {"ssid": "a\nb"}, {}):
        assert client.post(url, json=bad, headers=auth).status_code == 422, bad
    assert client.post(url, json={"ssid": "Home"}).status_code == 401


def test_other_user_cannot_change_wifi(client, auth, paired):
    pid, _sp = paired
    other = client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "slate-orbit-77",
                                                       "consent": True}).json()
    r = client.post(f"/api/v1/devices/{pid}/wifi", json={"ssid": "X"}, headers={"Authorization": f"Bearer {other['access_token']}"})
    assert r.status_code == 404


def test_switch_succeeds_and_is_reported(client, auth, paired, caplog):
    pid, sp = paired
    sp.provision_wifi("Old", "oldpassword")
    sp.networks = {"Old": {"password": "oldpassword"}, "New": {"password": SECRET}}
    caplog.set_level(logging.DEBUG)
    ws_cm, ws = online(client, sp)
    try:
        r = client.post(f"/api/v1/devices/{pid}/wifi", json={"ssid": "New", "password": SECRET}, headers=auth)
        assert r.status_code == 202 and r.json()["state"] == "pending" and r.json()["ssid"] == "New"
        assert "password" not in r.text
        sp.handle(ws, ws.receive_json())                        # команда set_wifi дошла до платы
        assert sp.ssid == "New" and sp.last_command == 5
        # второй запрос, пока идёт смена, отклоняется
        assert client.post(f"/api/v1/devices/{pid}/wifi", json={"ssid": "Other"}, headers=auth).status_code == 409
        assert sp.net_tick(ws, 2000) == 1                           # сервер на связи в новой сети → подтверждено
        st = wait_state(client, auth, pid, "ok")
        assert st["ssid"] == "New" and st["error"] == ""
        assert sp.ssid == "New"
    finally:
        ws_cm.__exit__(None, None, None)
    # пароль нигде не остаётся: ни в статусе, ни в реестре, ни в журнале сервера
    assert all(SECRET not in str(v) for v in registry.wifi_changes.values())
    assert SECRET not in caplog.text


def test_failed_switch_rolls_back_to_old_network(client, auth, paired):
    pid, sp = paired
    sp.provision_wifi("Old", "oldpassword")
    sp.networks = {"Old": {"password": "oldpassword"}, "Typo": {"password": "the-real-password"}}
    ws_cm, ws = online(client, sp)
    try:
        client.post(f"/api/v1/devices/{pid}/wifi", json={"ssid": "Typo", "password": "wrong-password"}, headers=auth)
        sp.handle(ws, ws.receive_json())
        assert sp.ssid == "Typo"
        assert sp.net_tick(ws, 30000) == 0                          # ещё пробуем
        assert wait_state(client, auth, pid, "pending")
        assert sp.net_tick(ws, 20000) == 2                          # 50 с > 45 с: откат на прежнюю сеть
        assert (sp.ssid, sp.wifi_password) == ("Old", "oldpassword")
        st = wait_state(client, auth, pid, "failed")            # итог отправлен уже из прежней сети
        assert st["error"] == "connect_failed"
    finally:
        ws_cm.__exit__(None, None, None)
    # прежняя сеть сохранена в NVS: после перезагрузки плата вернётся в неё
    sp.boot()
    assert (sp.ssid, sp.wifi_password) == ("Old", "oldpassword")


def test_switch_rolls_back_when_server_unreachable_in_new_network(client, auth, paired):
    """Wi-Fi подключился, но сервера в этой сети нет — тоже откат."""
    pid, sp = paired
    sp.provision_wifi("Old", "oldpassword")
    sp.networks = {"Old": {"password": "oldpassword"}, "Guest": {"password": "guestpass1", "server_ok": False}}
    ws_cm, ws = online(client, sp)
    try:
        client.post(f"/api/v1/devices/{pid}/wifi", json={"ssid": "Guest", "password": "guestpass1"}, headers=auth)
        sp.handle(ws, ws.receive_json())
        assert sp.net_tick(ws, 46000) == 2
        assert sp.ssid == "Old"
        wait_state(client, auth, pid, "failed")
    finally:
        ws_cm.__exit__(None, None, None)


def test_invalid_command_is_ignored_by_firmware(client, auth, paired):
    _pid, sp = paired
    sp.provision_wifi("Old", "oldpassword")
    kind, cmd = sp.core.parse_message({"type": "command", "name": "set_wifi", "ssid": "x" * 40}, sp.settings)
    assert cmd.cmd == 0                                          # слишком длинное имя сети отброшено прошивкой


def test_bluetooth_reconfigure_keeps_identity(client, auth, paired):
    """Окно повторной настройки: приложение шлёт только новый адрес сервера и Wi-Fi — device_id и токен прежние."""
    pid, sp = paired
    old_link = dict(sp.link_data)
    sp.provision_wifi("Old", "oldpassword")
    sp.enter_reconfigure()
    assert sp.reconfigure_mode and sp.link_data == old_link      # флаг в NVS пережил «перезагрузку»
    assert sp.pair_update({"server": "10.176.189.233:8000", "tls": False})
    sp.provision_wifi("Omorriz", "12345678")
    assert sp.link_data["server"] == "10.176.189.233:8000"
    assert sp.link_data["device_id"] == old_link["device_id"] and sp.link_data["token"] == old_link["token"]
    sp.finish_reconfigure()
    assert not sp.reconfigure_mode and sp.ssid == "Omorriz"
    # недопустимое обновление ничего не меняет
    assert not sp.pair_update({"server": "bad/host?x=1"})
    assert sp.link_data["server"] == "10.176.189.233:8000"
    # личность прежняя: сервер по-прежнему узнаёт плату
    sp.link_data["server"] = "testserver"
    with connect(client, sp) as ws:
        assert ws.receive_json()["type"] == "hello"
    assert client.get(f"/api/v1/devices/{pid}", headers=auth).json()["public_id"] == pid
