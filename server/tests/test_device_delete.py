"""Удаление колонки: онлайн-плата получает сброс к заводским настройкам; плата с удалённым идентификатором — тоже."""
import pytest
from starlette.websockets import WebSocketDisconnect

from tests.helpers import open_ws, paired


def test_delete_online_device_sends_factory_reset(client, auth):
    pid, token = paired(client, auth)
    ws, w, _ = open_ws(client, pid, token)
    try:
        assert client.delete(f"/api/v1/devices/{pid}", headers=auth).status_code == 204
        cmd = w.receive_json()
        assert cmd == {"type": "command", "name": "factory_reset"}
    finally:
        ws.__exit__(None, None, None)
    assert client.get(f"/api/v1/devices/{pid}", headers=auth).status_code == 404


def test_delete_offline_device(client, auth):
    pid, _ = paired(client, auth)
    assert client.delete(f"/api/v1/devices/{pid}", headers=auth).status_code == 204
    assert client.get("/api/v1/devices", headers=auth).json() == []


def test_deleted_identity_gets_factory_reset_not_endless_403(client, auth):
    pid, token = paired(client, auth)
    assert client.delete(f"/api/v1/devices/{pid}", headers=auth).status_code == 204
    with client.websocket_connect(f"/noctis/v1/?device_id={pid}&token={token}") as w:
        assert w.receive_json() == {"type": "command", "name": "factory_reset"}


def test_wrong_token_for_existing_device_is_still_rejected(client, auth):
    pid, _ = paired(client, auth)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/noctis/v1/?device_id={pid}&token=wrong") as w:
            w.receive_json()


def test_disabled_device_is_rejected_not_reset(client, auth):
    pid, token = paired(client, auth)
    client.patch(f"/api/v1/devices/{pid}", json={"enabled": False}, headers=auth)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/noctis/v1/?device_id={pid}&token={token}") as w:
            w.receive_json()
