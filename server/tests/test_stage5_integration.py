"""Этап 5: виртуальная колонка (настоящий Си-код прошивки: расписание, часы, плеер) против настоящего сервера.
Проверяется цепочка «голос → таймер/будильник на сервере → снимок на плату → NVS → срабатывание → звонок → остановка»,
перезагрузка платы, повреждённый NVS, работа без связи с сервером и приём музыкального потока."""
import json
import ctypes
import math
import struct
import time
import wave
from datetime import datetime, timedelta, timezone

import pytest

from noctis_server import registry, skills
from noctis_server import scheduler as sch
from noctis_server.config import settings
from noctis_server.media import player as media_player
from noctis_server.voice import gateway as gw_module
from tools.virtual_speaker import PLAYER_IDLE, PLAYER_PLAYING  # noqa: E402

from .conftest import run_async
from .helpers import receive
from .test_firmware_integration import connect, core, paired  # noqa: F401  (фикстуры)

UTC = timezone.utc
FRI_0730_MSK = 1792125000          # 2026-10-16 07:30 по Москве (пятница) = 04:30 UTC


def pump(ws, sp, until, limit=3000, timeout=20.0):
    """Читает сообщения сервера и применяет их как прошивка, пока until(список_типов, сообщение) не вернёт True."""
    seen = []
    for _ in range(limit):
        m = receive(ws, timeout)
        sp.handle(ws, m)
        seen.append(m)
        if until(seen, m):
            return seen
    raise AssertionError("нужное сообщение не пришло: " + str([x["type"] for x in seen][-10:]))


def handshake(client, ws, sp):
    sp.handle(ws, ws.receive_json())           # hello
    sp.handle(ws, ws.receive_json())           # config
    sp.send_device_info(ws)
    return pump(ws, sp, lambda seen, m: m["type"] == "schedule")      # time_sync, schedule


def reply_complete(seen):
    ended = any(x["type"] == "audio_response_end" for x in seen)
    return ended and any(x["type"] == "text_response" and not x["text"].startswith("Вы сказали") for x in seen)


def ask(ws, sp, assistant, text):
    """Говорит команду; возвращает сообщения сервера до конца ответа (плата применяет их как прошивка)."""
    assistant.stt_text = text
    sp.speak_command(ws, b"\x02\x00" * 8000)
    return pump(ws, sp, lambda seen, m: reply_complete(seen))


@pytest.fixture(autouse=True)
def fast_env(monkeypatch, tmp_path):
    monkeypatch.setattr(media_player, "realtime", False)
    monkeypatch.setattr(gw_module, "AFTER_REPLY_DELAY_SEC", 0)
    monkeypatch.setattr(settings, "music_dir", str(tmp_path / "music"))


# ------------------------------------------------------------------ время и расписание после подключения
def test_time_sync_and_schedule_reach_the_board_and_survive_reboot(client, auth, paired):
    pid, sp = paired
    client.post(f"/api/v1/devices/{pid}/alarms", json={"hour": 7, "minute": 30, "weekdays": [0, 1, 2, 3, 4],
                                                         "label": "работа", "melody": "soft"}, headers=auth)
    with connect(client, sp) as ws:
        seen = handshake(client, ws, sp)
        assert [m["type"] for m in seen] == ["time_sync", "schedule"]
        assert sp.sched.n_alarms == 1 and sp.sched.alarms[0].weekdays == 0b11111
        assert sp.sched.alarms[0].melody == b"soft"
        assert sp.epoch is not None and abs(sp.epoch - time.time()) < 5          # время от сервера (NTP нет)
        assert sp.utc_offset == 10800
        assert sp.clock_text() == (datetime.now(UTC) + timedelta(hours=3)).strftime("%H:%M")
    # перезагрузка: время неизвестно, расписание и пояс — из NVS
    sp.boot()
    assert sp.epoch is None and sp.clock_text() == "--:--" and sp.sched_loaded
    assert sp.sched.n_alarms == 1 and sp.utc_offset == 10800


def test_ntp_time_wins_over_server_time(client, auth, paired):
    pid, sp = paired
    sp.set_clock(FRI_0730_MSK + 86400 * 100, sntp=True)           # NTP уже дал время
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
    assert sp.epoch == FRI_0730_MSK + 86400 * 100                  # серверное время не затёрло NTP


# ------------------------------------------------------------------ голос → таймер → звонок → остановка
def test_voice_timer_rings_on_board_and_is_announced(client, auth, assistant, paired):
    pid, sp = paired
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
        msgs = ask(ws, sp, assistant, "Поставь таймер на 5 минут")
        assert any(m["type"] == "text_response" and m["text"] == "Таймер на пять минут запущен." for m in msgs)
        assert sp.sched.n_timers == 1 and sp.summary()["timer_left"] in range(295, 301)

        sp.advance(299)
        assert sp.tick(ws) == [] and not sp.ringing                 # ещё не время
        sp.advance(2)
        events = sp.tick(ws)
        assert [(e["kind"], e["id"]) for e in events] == [("timer", sp.sched.timers[0].id)] and sp.ringing
        assert sp.sent[-1] == {"type": "sched_event", "kind": "timer", "id": sp.sched.timers[0].id}
        assert sp.tick(ws) == []                                    # не повторяется

        # сервер услышал событие платы → голосовое уведомление → плата замолкает
        msgs = pump(ws, sp, lambda seen, m: m["type"] == "audio_response_end")
        assert any(m["type"] == "text_response" and m["text"] == "Таймер завершён." for m in msgs)
        assert not sp.ringing
    # сработавший таймер не оживает после перезагрузки платы
    sp.boot()
    sp.set_clock(FRI_0730_MSK + 86400)
    assert sp.tick() == [] and sp.sched.n_timers == 0      # сервер убрал сработавший таймер из снимка


def test_wake_word_stop_is_reported_to_server(client, auth, assistant, paired):
    pid, sp = paired
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
        registry.ringing[pid] = time.time() + 60                      # сервер считает, что звонит
        sp.ringing = True
        sp.stop_ring(ws, notify=True)                                 # слово пробуждения/кнопка
        assert not sp.ringing
        ws.send_json({"type": "ping"})
        assert receive(ws)["type"] == "pong"                          # сообщения обрабатываются по порядку
        assert pid not in registry.ringing
        sp.stop_ring(ws, notify=True)                                 # повторная остановка ничего не шлёт
        ws.send_json({"type": "ping"})
        assert receive(ws)["type"] == "pong"


def test_voice_stop_while_ringing_sends_stop_ring_command(client, auth, assistant, paired):
    pid, sp = paired
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
        gw = client.app.state.gateway
        client.post(f"/api/v1/devices/{pid}/timers", json={"seconds": 30}, headers=auth)
        pump(ws, sp, lambda seen, m: m["type"] == "schedule")
        run_async(client, gw.scheduler.tick, datetime.now(UTC) + timedelta(seconds=60))      # срабатывание на сервере
        pump(ws, sp, lambda seen, m: m["type"] == "audio_response_end")                       # голосовое уведомление
        assert sch.is_ringing(pid)
        sp.ringing = True                                             # плата ещё звонит (мелодия началась раньше голоса)
        msgs = ask(ws, sp, assistant, "Стоп")
        assert {"type": "command", "name": "stop_ring"} in msgs and not sp.ringing
        assert any(m["type"] == "text_response" and m["text"] == "Выключаю." for m in msgs)
        assert not sch.is_ringing(pid)


# ------------------------------------------------------------------ будильник без связи с сервером
def test_alarm_rings_offline_after_reboot_once_time_is_known(client, auth, paired):
    pid, sp = paired
    client.post(f"/api/v1/devices/{pid}/alarms", json={"hour": 7, "minute": 30, "weekdays": [0, 1, 2, 3, 4],
                                                         "label": "работа", "melody": "rooster"}, headers=auth)
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
    sp.boot()                                                         # питание пропало и вернулось; сервера нет
    assert sp.tick() == [] and not sp.ringing                         # времени ещё нет — молчим
    sp.set_clock(FRI_0730_MSK - 120)                                  # NTP дал время: пятница 07:28
    assert sp.tick() == []
    sp.advance(120)                                                   # 07:30
    events = sp.tick()                                                # связи нет → ws=None
    assert [e["kind"] for e in events] == ["alarm"] and sp.ringing and sp.ring_melody == "rooster"
    assert sp.tick() == [] and sp.clock_text() == "07:30"             # та же минута — один звонок
    sp.stop_ring()
    sp.advance(86400)                                                 # суббота 07:30 — выходной
    assert sp.tick() == []
    sp.advance(2 * 86400)                                             # понедельник 07:30
    assert [e["kind"] for e in sp.tick()] == ["alarm"]
    # звонок не повторяется после ещё одной перезагрузки в ту же минуту
    sp.boot()
    sp.set_clock(FRI_0730_MSK + 3 * 86400 + 20)
    assert sp.tick() == []


def test_summary_for_home_screen(client, auth, assistant, paired):
    pid, sp = paired
    client.post(f"/api/v1/devices/{pid}/alarms", json={"hour": 7, "minute": 30, "weekdays": [0, 1, 2, 3, 4]}, headers=auth)
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
        ask(ws, sp, assistant, "Поставь таймер на 90 секунд")
    s = sp.summary()
    assert s["alarm"] == (7, 30) and 85 <= s["timer_left"] <= 91


def test_damaged_nvs_schedule_is_ignored_and_resynced(client, auth, paired):
    pid, sp = paired
    client.post(f"/api/v1/devices/{pid}/alarms", json={"hour": 6, "minute": 0, "weekdays": [1]}, headers=auth)
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
    data = json.loads(sp.nvs_path.read_text())
    blob = bytearray(bytes.fromhex(data["sched_hex"]))
    blob[10] ^= 0xFF                                                  # порча одного байта (CRC32 не совпадёт)
    data["sched_hex"] = blob.hex()
    sp.nvs_path.write_text(json.dumps(data))
    sp.boot()
    assert not sp.sched_loaded and sp.sched.n_alarms == 0 and sp.sched.n_timers == 0
    sp.set_clock(FRI_0730_MSK)
    assert sp.tick() == []                                            # без данных ничего не звонит и не падает
    with connect(client, sp) as ws:                                   # сервер присылает расписание заново
        handshake(client, ws, sp)
    assert sp.sched.n_alarms == 1


def test_sched_garbage_from_server_does_not_break_board(client, auth, paired):
    pid, sp = paired
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
    before = sp.core.sched_blob(sp.sched)
    for bad in ({"type": "schedule", "rev": "x", "timers": "no", "alarms": 5},
                {"type": "schedule", "timers": [{"id": 1}], "alarms": [{"id": 2, "h": 99, "m": 0, "wd": 1}]},
                {"type": "time_sync", "epoch": 5, "utc_offset": 0}, {"type": "time_sync", "epoch": "x"}):
        sp.handle(None, bad)
    assert sp.sched.n_alarms == 0 and sp.sched.n_timers == 0                    # мусорные элементы пропущены, состояние целое
    assert sp.epoch is None or sp.epoch > 1_700_000_000                         # нелепое время (1970 год) не принято
    assert before and sp.core.sched_blob(sp.sched)


# ------------------------------------------------------------------ музыка
def wav_file(path, seconds=2.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 9))) for i in range(int(16000 * seconds))))


def test_music_stream_is_buffered_and_played_only_when_speaker_is_idle(client, auth, assistant, paired, tmp_path):
    pid, sp = paired
    wav_file(tmp_path / "song.wav", seconds=2.0)
    client.put("/api/v1/media/library/song.wav", content=(tmp_path / "song.wav").read_bytes(), headers=auth)
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
        ask(ws, sp, assistant, "Включи музыку")
        pump(ws, sp, lambda seen, m: m["type"] == "music_start")
        assert sp.player.title == b"song"
        # динамик занят диалогом: данные копятся, но не проигрываются
        sp.audio_idle = False
        pump(ws, sp, lambda seen, m: m["type"] == "music_chunk" and m["index"] >= 3)
        assert sp.pump_music() == 0 and sp.music_pcm == b""
        sp.audio_idle = True
        pump(ws, sp, lambda seen, m: m["type"] == "music_end")
        total = 0
        while True:
            n = sp.pump_music(3200)
            total += n
            if n == 0:
                break
        assert total == 2 * 16000 * 2 and sp.music_state == PLAYER_IDLE        # 2 с PCM16 16 кГц, трек доигран до конца
        assert sp.player.overruns == 0 and len(sp.music_pcm) == total


def test_music_pause_resume_stop_reach_the_board(client, auth, assistant, paired, tmp_path, monkeypatch):
    pid, sp = paired
    wav_file(tmp_path / "a.wav", seconds=30.0)
    client.put("/api/v1/media/library/a.wav", content=(tmp_path / "a.wav").read_bytes(), headers=auth)
    monkeypatch.setattr(media_player, "realtime", True)
    monkeypatch.setattr(media_player, "LEAD_SEC", 0.0)
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
        ask(ws, sp, assistant, "Включи музыку")
        pump(ws, sp, lambda seen, m: m["type"] == "music_chunk" and m["index"] >= 2)
        assert sp.music_state in (1, PLAYER_PLAYING)                                      # набор предзапаса или игра
        ask(ws, sp, assistant, "Пауза")
        assert sp.player.paused is True and sp.pump_music() == 0                # пауза: данные не уходят на динамик
        ask(ws, sp, assistant, "Продолжи")
        pump(ws, sp, lambda seen, m: m["type"] == "music_resume")                       # действие идёт после ответа-подтверждения
        assert sp.player.paused is False
        ask(ws, sp, assistant, "Стоп")
        pump(ws, sp, lambda seen, m: m["type"] == "music_stop")
        assert sp.music_state == PLAYER_IDLE and sp.pump_music() == 0


def test_alarm_silences_music_on_board(client, auth, assistant, paired, tmp_path, monkeypatch):
    pid, sp = paired
    wav_file(tmp_path / "a.wav", seconds=30.0)
    client.put("/api/v1/media/library/a.wav", content=(tmp_path / "a.wav").read_bytes(), headers=auth)
    monkeypatch.setattr(media_player, "realtime", True)
    monkeypatch.setattr(media_player, "LEAD_SEC", 0.0)
    with connect(client, sp) as ws:
        handshake(client, ws, sp)
        ask(ws, sp, assistant, "Включи музыку")
        pump(ws, sp, lambda seen, m: m["type"] == "music_chunk" and m["index"] >= 2)
        client.post(f"/api/v1/devices/{pid}/timers", json={"seconds": 30}, headers=auth)
        pump(ws, sp, lambda seen, m: m["type"] == "schedule")
        sp.advance(31)
        sp.tick(ws)
        assert sp.ringing and sp.music_state == PLAYER_IDLE                      # плата сама заглушила музыку


def test_struct_layouts_match_between_python_and_c(core):
    from tools.virtual_speaker import Player, SchedState
    assert core.lib.sched_state_size() == ctypes.sizeof(SchedState)
    assert core.lib.noctis_player_size() == ctypes.sizeof(Player)
