"""Общие помощники тестов этапа 5: диалог с «устройством» по WebSocket."""
import base64
import time


def paired(client, auth, name="T"):
    d = client.post("/api/v1/devices", json={"name": name}, headers=auth).json()
    return d["public_id"], d["device_token"]


def open_ws(client, pid, token, introduce=True):
    """Подключение устройства; introduce=True — как прошивка, шлёт device_info и получает time_sync + schedule."""
    ws = client.websocket_connect(f"/noctis/v1/?device_id={pid}&token={token}")
    w = ws.__enter__()
    assert w.receive_json()["type"] == "hello"
    assert w.receive_json()["type"] == "config"
    hs = {}
    if introduce:
        w.send_json({"type": "device_info", "firmware": "0.4.0", "config_revision": 1, "free_heap": 100000,
                     "reset_reason": "poweron"})
        for _ in range(2):
            m = w.receive_json()
            hs[m["type"]] = m
    return ws, w, hs


def record(ws, nbytes=16000):
    ws.send_json({"type": "start_recording"})
    ws.send_json({"type": "audio_chunk", "index": 0, "data": base64.b64encode(b"\x02\x00" * (nbytes // 2)).decode()})
    ws.send_json({"type": "stop_recording"})


def receive(ws, timeout=20.0):
    """receive_json с ограничением по времени (зависший тест лучше упавшего по таймауту, чем вечный)."""
    deadline = time.monotonic() + timeout
    while ws._send_rx.statistics().current_buffer_used == 0:
        if time.monotonic() > deadline:
            raise TimeoutError("сообщение от сервера не пришло")
        time.sleep(0.003)
    return ws.receive_json()


def read_until(ws, predicate, limit=3000, timeout=20.0):
    """Читает сообщения, пока predicate(список_прочитанного) не станет истинным."""
    msgs = []
    for _ in range(limit):
        try:
            msgs.append(receive(ws, timeout))
        except TimeoutError:
            summary = [m["type"] + (":" + m.get("text", "")[:30] if m["type"] == "text_response" else "") for m in msgs
                       if m["type"] != "music_chunk" and m["type"] != "audio_response_chunk"]
            raise AssertionError(f"нужное сообщение не пришло за {timeout} с; получено: {summary}")
        if predicate(msgs):
            return msgs
    raise AssertionError(f"нужное сообщение не пришло: {[m['type'] for m in msgs][-10:]}")


def reply_done(msgs):
    """Ответ закончен: есть конец аудио и текст ответа (не «Вы сказали»)."""
    ended = any(m["type"] == "audio_response_end" for m in msgs)
    texts = [m["text"] for m in msgs if m["type"] == "text_response" and not m["text"].startswith("Вы сказали")]
    return ended and bool(texts)


def wait_idle(ws, timeout=10.0):
    """Ждёт, пока сервер дообработает предыдущую реплику: новая команда во время обработки игнорируется (как у прошивки)."""
    gw = ws.app.state.gateway
    deadline = time.monotonic() + timeout
    while any(c.processing_task is not None for c in list(gw.active.values())):
        if time.monotonic() > deadline:
            raise AssertionError("сервер не закончил обработку реплики")
        time.sleep(0.003)


def say(ws, assistant, text, until=reply_done):
    """Говорит команду; возвращает все сообщения до конца ответа."""
    wait_idle(ws)
    assistant.stt_text = text
    record(ws)
    msgs = read_until(ws, until, limit=3000)
    wait_idle(ws)
    return msgs


def reply_text(msgs):
    return [m["text"] for m in msgs if m["type"] == "text_response" and not m["text"].startswith("Вы сказали")][-1]


def of_type(msgs, kind):
    return [m for m in msgs if m["type"] == kind]
