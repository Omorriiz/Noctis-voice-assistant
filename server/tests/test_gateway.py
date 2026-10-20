import base64

import pytest
from starlette.websockets import WebSocketDisconnect

from noctis_server.config import settings
from noctis_server.db import SessionLocal
from noctis_server.models import HistoryEntry


def record(ws, seconds_bytes=16000):
    ws.send_json({"type": "start_recording"})
    ws.send_json({"type": "audio_chunk", "index": 0, "data": base64.b64encode(b"\x02\x00" * (seconds_bytes // 2)).decode()})
    ws.send_json({"type": "stop_recording"})


def collect(ws):
    """Читает сообщения до audio_response_end и text_response с ответом."""
    msgs, ended = [], False
    while True:
        m = ws.receive_json()
        msgs.append(m)
        if m["type"] == "audio_response_end":
            ended = True
        if ended and m["type"] == "text_response" and not m["text"].startswith("Вы сказали"):
            return msgs


def paired_device(client, auth):
    d = client.post("/api/v1/devices", json={"name": "T"}, headers=auth).json()
    return d["public_id"], d["device_token"]


def test_authenticated_device_full_cycle_saves_history(client, auth, assistant):
    pid, token = paired_device(client, auth)
    with client.websocket_connect(f"/noctis/v1/?device_id={pid}&token={token}") as ws:
        assert ws.receive_json()["type"] == "hello"
        cfg = ws.receive_json()
        assert cfg["type"] == "config" and cfg["voice_gender"] == "male" and cfg["wake_word"] == "nils"
        assert client.get(f"/api/v1/devices/{pid}", headers=auth).json()["online"] is True

        record(ws)
        msgs = collect(ws)
        types = [m["type"] for m in msgs]
        assert types[0] == "text_response" and msgs[0]["text"] == "Вы сказали: расскажи что нибудь интересное"
        assert "audio_response_start" in types and "audio_response_chunk" in types
        assert msgs[-1]["text"] == "Сейчас вечер. Хорошего дня."

    assert client.get(f"/api/v1/devices/{pid}", headers=auth).json()["online"] is False
    rows = client.get(f"/api/v1/devices/{pid}/history", headers=auth).json()
    assert len(rows) == 1 and rows[0]["user_text"] == "расскажи что нибудь интересное"
    # аудио не сохраняется на диск по умолчанию
    assert settings.store_audio is False


def test_history_respects_privacy_setting(client, auth):
    client.patch("/api/v1/me/settings", json={"history_enabled": False}, headers=auth)
    pid, token = paired_device(client, auth)
    with client.websocket_connect(f"/noctis/v1/?device_id={pid}&token={token}") as ws:
        ws.receive_json(); ws.receive_json()
        record(ws)
        collect(ws)
    with SessionLocal() as db:
        assert db.query(HistoryEntry).count() == 0


def test_bad_token_rejected(client, auth):
    pid, _ = paired_device(client, auth)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/noctis/v1/?device_id={pid}&token=wrong") as ws:
            ws.receive_json()


def test_anonymous_allowed_only_when_enabled(client):
    with client.websocket_connect("/nils/v1/") as ws:  # старый путь, старая прошивка
        assert ws.receive_json()["type"] == "hello"
    settings.allow_anonymous_devices = False
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/noctis/v1/") as ws:
            ws.receive_json()


def test_settings_change_is_pushed_live(client, auth):
    pid, token = paired_device(client, auth)
    with client.websocket_connect(f"/noctis/v1/?device_id={pid}&token={token}") as ws:
        ws.receive_json(); ws.receive_json()
        client.patch(f"/api/v1/devices/{pid}/settings", json={"voice_gender": "female", "character_id": "calm"}, headers=auth)
        pushed = ws.receive_json()
        assert pushed["type"] == "config" and pushed["voice_gender"] == "female" and pushed["revision"] == 2


def test_reboot_command_delivered(client, auth):
    pid, token = paired_device(client, auth)
    with client.websocket_connect(f"/noctis/v1/?device_id={pid}&token={token}") as ws:
        ws.receive_json(); ws.receive_json()
        assert client.post(f"/api/v1/devices/{pid}/reboot", headers=auth).status_code == 202
        assert ws.receive_json() == {"type": "command", "name": "reboot"}


def test_each_device_has_own_history_and_settings(client, auth, assistant):
    (p1, t1), (p2, t2) = paired_device(client, auth), paired_device(client, auth)
    client.patch(f"/api/v1/devices/{p2}/settings", json={"character_id": "sarcastic"}, headers=auth)
    with client.websocket_connect(f"/noctis/v1/?device_id={p1}&token={t1}") as w1, \
         client.websocket_connect(f"/noctis/v1/?device_id={p2}&token={t2}") as w2:
        for w in (w1, w2):
            w.receive_json(); w.receive_json()
        record(w2)
        collect(w2)
    llm_calls = [c for c in assistant.calls if c[0] == "llm"]
    assert llm_calls[0][2]["character_id"] == "sarcastic"  # настройки именно этого устройства дошли до LLM-слоя


def test_too_short_audio_gets_spoken_hint(client):
    with client.websocket_connect("/noctis/v1/") as ws:
        ws.receive_json(); ws.receive_json()
        record(ws, seconds_bytes=100)
        msgs = []
        while not any(m["type"] == "audio_response_end" for m in msgs):
            msgs.append(ws.receive_json())
        assert any(m["type"] == "text_response" and "коротко" in m["text"] for m in msgs)
