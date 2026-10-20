"""Таймеры и будильники: логика расписания, REST-API, голосовые команды по WebSocket, срабатывание и уведомления."""
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from noctis_server import registry, skills
from noctis_server import scheduler as sch
from noctis_server.db import SessionLocal
from noctis_server.intents import parse
from noctis_server.models import Alarm, Device, Timer

from .conftest import register, run_async
from .helpers import of_type, open_ws, paired, reply_done, reply_text, say

UTC = timezone.utc


def at(local: str, tz: str = "Europe/Moscow") -> datetime:
    """Местное время → aware UTC. 2026-10-16 — пятница."""
    return datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)


NOW = at("2026-10-16 12:00")


@pytest.fixture
def dev(client, auth):
    pid, _ = paired(client, auth)
    with SessionLocal() as db:
        yield db, db.scalar(select(Device).where(Device.public_id == pid))


# ------------------------------------------------------------------ таймеры: логика
def test_timer_lifecycle(dev):
    db, d = dev
    t = sch.create_timer(db, d, 300, now=NOW)
    assert sch.remaining_sec(t, NOW + timedelta(seconds=60)) == 240
    assert sch.pause_timer(db, t, NOW + timedelta(seconds=60)) and t.state == "paused" and t.ends_at is None
    assert t.remaining_sec == 240 and not sch.pause_timer(db, t)
    assert sch.resume_timer(db, t, NOW + timedelta(seconds=100)) and t.state == "running"
    assert sch.collect_due(db, NOW + timedelta(seconds=339)) == []
    fired = sch.collect_due(db, NOW + timedelta(seconds=341))
    assert [(f.kind, f.item_id) for f in fired] == [("timer", t.id)] and t.state == "finished"
    assert sch.collect_due(db, NOW + timedelta(seconds=400)) == []          # повторно не срабатывает
    assert not sch.cancel_timer(db, t)


def test_timer_limits(dev):
    db, d = dev
    for bad in (0, -5, sch.MAX_TIMER_SEC + 1):
        with pytest.raises(sch.ScheduleError, match="duration"):
            sch.create_timer(db, d, bad)
    for _ in range(sch.MAX_TIMERS):
        sch.create_timer(db, d, 60)
    with pytest.raises(sch.ScheduleError, match="limit"):
        sch.create_timer(db, d, 60)
    sch.cancel_timer(db, sch.active_timers(db, d)[0])
    sch.create_timer(db, d, 60)       # место освободилось


# ------------------------------------------------------------------ будильники: логика
def test_weekday_alarm_fires_once_per_minute_and_only_on_its_days(dev):
    db, d = dev
    a = sch.create_alarm(db, d, 7, 30, [0, 1, 2, 3, 4], now=NOW)
    assert sch.collect_due(db, at("2026-10-16 07:29")) == []
    assert [f.item_id for f in sch.collect_due(db, at("2026-10-16 07:30"))] == [a.id]       # пятница
    assert sch.collect_due(db, at("2026-10-16 07:30") + timedelta(seconds=30)) == []         # та же минута
    assert sch.collect_due(db, at("2026-10-17 07:30")) == []                                 # суббота
    assert len(sch.collect_due(db, at("2026-10-19 07:30"))) == 1                             # понедельник
    assert a.enabled                                                                         # повторяющийся остаётся


def test_one_shot_alarm_fires_then_disables(dev):
    db, d = dev
    a = sch.create_alarm(db, d, 7, 30, [], now=NOW)
    assert a.one_shot_date == "2026-10-17"                         # 7:30 уже прошло → завтра
    assert sch.collect_due(db, at("2026-10-16 07:30")) == []
    assert len(sch.collect_due(db, at("2026-10-17 07:30"))) == 1 and a.enabled is False
    assert sch.collect_due(db, at("2026-10-17 07:30")) == []


def test_alarm_uses_device_timezone(client, auth):
    pid, _ = paired(client, auth)
    assert client.patch(f"/api/v1/devices/{pid}/settings", json={"timezone": "America/New_York"}, headers=auth).status_code == 200
    with SessionLocal() as db:
        d = db.scalar(select(Device).where(Device.public_id == pid))
        a = sch.create_alarm(db, d, 7, 30, [4], now=NOW)
        assert sch.collect_due(db, at("2026-10-16 07:30", "Europe/Moscow")) == []
        assert [f.item_id for f in sch.collect_due(db, at("2026-10-16 07:30", "America/New_York"))] == [a.id]


def test_next_occurrence_across_midnight_and_weekdays():
    late = at("2026-10-16 23:50")
    assert sch.next_occurrence(0, 10, [], "Europe/Moscow", late).strftime("%Y-%m-%d %H:%M") == "2026-10-17 00:10"
    assert sch.next_occurrence(7, 0, [0], "Europe/Moscow", NOW).strftime("%Y-%m-%d") == "2026-10-19"   # ближайший понедельник
    assert sch.next_occurrence(12, 0, [], "Europe/Moscow", NOW).strftime("%Y-%m-%d") == "2026-10-17"   # 12:00 уже наступило
    assert sch.next_occurrence(6, 0, [], "Europe/Moscow", NOW, day_offset=1).strftime("%Y-%m-%d") == "2026-10-17"


def test_alarm_validation_and_limit(dev):
    db, d = dev
    for args in ((24, 0), (7, 60), (-1, 0)):
        with pytest.raises(sch.ScheduleError, match="time"):
            sch.create_alarm(db, d, *args)
    with pytest.raises(sch.ScheduleError, match="melody"):
        sch.create_alarm(db, d, 7, 0, melody="../../etc/passwd")
    with pytest.raises(sch.ScheduleError, match="weekdays"):
        sch.create_alarm(db, d, 7, 0, [7])
    with pytest.raises(sch.ScheduleError, match="date"):
        sch.create_alarm(db, d, 7, 0, one_shot_date="2026-13-45")
    for _ in range(sch.MAX_ALARMS):
        sch.create_alarm(db, d, 7, 0, [1])
    with pytest.raises(sch.ScheduleError, match="limit"):
        sch.create_alarm(db, d, 8, 0, [1])


def test_snapshot_for_device(dev):
    db, d = dev
    running = sch.create_timer(db, d, 120, "чай")
    paused = sch.create_timer(db, d, 60)
    sch.pause_timer(db, paused)
    sch.create_alarm(db, d, 7, 30, [0, 2, 4], "работа", "soft")
    off = sch.create_alarm(db, d, 9, 0, [5])
    off.enabled = False
    one = sch.create_alarm(db, d, 6, 15, [], one_shot_date="2026-12-31")
    db.commit()
    snap = sch.schedule_snapshot(db, d)
    assert snap["type"] == "schedule"
    assert [t["id"] for t in snap["timers"]] == [running.id] and snap["timers"][0]["label"] == "чай"
    assert snap["timers"][0]["end"] == int(running.ends_at.replace(tzinfo=UTC).timestamp())
    by_h = {a["h"]: a for a in snap["alarms"]}
    assert set(by_h) == {7, 6} and by_h[7]["wd"] == 0b10101 and by_h[7]["date"] == 0 and by_h[7]["melody"] == "soft"
    assert by_h[6]["wd"] == 0 and by_h[6]["date"] == 20261231 and one.id == by_h[6]["id"]


def test_time_sync_message():
    m = sch.time_sync_message("Europe/Moscow", NOW)
    assert m == {"type": "time_sync", "epoch": int(NOW.timestamp()), "utc_offset": 10800}
    assert sch.time_sync_message("Nowhere/Land", NOW)["utc_offset"] == 10800           # неизвестный пояс → по умолчанию


# ------------------------------------------------------------------ REST
def test_timers_rest_crud_and_ownership(client, auth):
    pid, _ = paired(client, auth)
    r = client.post(f"/api/v1/devices/{pid}/timers", json={"seconds": 300, "label": "чай"}, headers=auth)
    assert r.status_code == 201 and r.json()["state"] == "running" and 298 <= r.json()["remaining_sec"] <= 300
    tid = r.json()["id"]
    assert client.post(f"/api/v1/devices/{pid}/timers/{tid}/pause", headers=auth).json()["state"] == "paused"
    assert client.post(f"/api/v1/devices/{pid}/timers/{tid}/pause", headers=auth).status_code == 409
    assert client.post(f"/api/v1/devices/{pid}/timers/{tid}/resume", headers=auth).json()["state"] == "running"
    assert client.post(f"/api/v1/devices/{pid}/timers/{tid}/explode", headers=auth).status_code == 404
    assert len(client.get(f"/api/v1/devices/{pid}/timers", headers=auth).json()) == 1
    assert client.post(f"/api/v1/devices/{pid}/timers", json={"seconds": 0}, headers=auth).status_code == 422
    assert client.post(f"/api/v1/devices/{pid}/timers", json={"seconds": 99999999}, headers=auth).status_code == 422
    # чужой пользователь не видит и не трогает
    other = {"Authorization": f"Bearer {register(client, 'b@example.com')['access_token']}"}
    assert client.get(f"/api/v1/devices/{pid}/timers", headers=other).status_code == 404
    assert client.delete(f"/api/v1/devices/{pid}/timers/{tid}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/devices/{pid}/timers/{tid}", headers=auth).status_code == 204
    assert client.get(f"/api/v1/devices/{pid}/timers", headers=auth).json() == []
    assert client.get(f"/api/v1/devices/{pid}/timers").status_code == 401


def test_timer_limit_over_rest(client, auth):
    pid, _ = paired(client, auth)
    for _ in range(sch.MAX_TIMERS):
        assert client.post(f"/api/v1/devices/{pid}/timers", json={"seconds": 60}, headers=auth).status_code == 201
    assert client.post(f"/api/v1/devices/{pid}/timers", json={"seconds": 60}, headers=auth).status_code == 409


def test_alarms_rest_crud(client, auth):
    pid, _ = paired(client, auth)
    r = client.post(f"/api/v1/devices/{pid}/alarms", json={"hour": 7, "minute": 30, "weekdays": [0, 1, 2, 3, 4],
                                                             "label": "работа", "melody": "rooster"}, headers=auth)
    assert r.status_code == 201 and r.json()["weekdays"] == [0, 1, 2, 3, 4] and r.json()["date"] is None
    aid = r.json()["id"]
    one = client.post(f"/api/v1/devices/{pid}/alarms", json={"hour": 6, "minute": 0}, headers=auth).json()
    assert one["weekdays"] == [] and one["date"]                                   # однократный: дата вычислена
    p = client.patch(f"/api/v1/devices/{pid}/alarms/{aid}", json={"enabled": False, "minute": 45}, headers=auth)
    assert p.json()["enabled"] is False and p.json()["minute"] == 45
    assert client.patch(f"/api/v1/devices/{pid}/alarms/{aid}", json={"melody": "nope"}, headers=auth).status_code == 422
    assert client.patch(f"/api/v1/devices/{pid}/alarms/{aid}", json={"weekdays": [9]}, headers=auth).status_code == 422
    for bad in ({"hour": 24, "minute": 0}, {"hour": 7, "minute": 60}, {"hour": 7, "minute": 0, "melody": "x"},
                {"hour": 7, "minute": 0, "weekdays": [8]}, {"hour": 7, "minute": 0, "date": "31.12.2026"}):
        assert client.post(f"/api/v1/devices/{pid}/alarms", json=bad, headers=auth).status_code == 422, bad
    assert len(client.get(f"/api/v1/devices/{pid}/alarms", headers=auth).json()) == 2
    other = {"Authorization": f"Bearer {register(client, 'c@example.com')['access_token']}"}
    assert client.delete(f"/api/v1/devices/{pid}/alarms/{aid}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/devices/{pid}/alarms/{aid}", headers=auth).status_code == 204
    assert client.delete(f"/api/v1/devices/{pid}/alarms/{aid}", headers=auth).status_code == 404


def test_device_time_and_invalid_timezone(client, auth):
    pid, _ = paired(client, auth)
    t = client.get(f"/api/v1/devices/{pid}/time", headers=auth).json()
    assert t["timezone"] == "Europe/Moscow" and t["utc_offset"] == 10800
    assert client.patch(f"/api/v1/devices/{pid}/settings", json={"timezone": "Mars/Olympus"}, headers=auth).status_code == 422
    assert client.post("/api/v1/devices", json={"name": "x", "timezone": "nope"}, headers=auth).status_code == 422
    assert client.patch(f"/api/v1/devices/{pid}/settings", json={"timezone": "Asia/Tokyo"}, headers=auth).status_code == 200
    assert client.get(f"/api/v1/devices/{pid}/time", headers=auth).json()["utc_offset"] == 32400


# ------------------------------------------------------------------ WebSocket: расписание → плата
def test_handshake_sends_time_and_schedule_and_changes_are_pushed(client, auth):
    pid, token = paired(client, auth)
    client.post(f"/api/v1/devices/{pid}/alarms", json={"hour": 7, "minute": 0, "weekdays": [1]}, headers=auth)
    ctx, ws, hs = open_ws(client, pid, token)
    try:
        assert hs["time_sync"]["utc_offset"] == 10800 and abs(hs["time_sync"]["epoch"] - datetime.now(UTC).timestamp()) < 5
        assert [a["wd"] for a in hs["schedule"]["alarms"]] == [2]
        client.post(f"/api/v1/devices/{pid}/timers", json={"seconds": 90, "label": "яйца"}, headers=auth)
        pushed = ws.receive_json()
        assert pushed["type"] == "schedule" and pushed["timers"][0]["label"] == "яйца"
        client.delete(f"/api/v1/devices/{pid}/alarms/{hs['schedule']['alarms'][0]['id']}", headers=auth)
        assert ws.receive_json()["alarms"] == []
    finally:
        ctx.__exit__(None, None, None)


# ------------------------------------------------------------------ голос: таймеры и будильники
def test_voice_timer_ring_and_stop(client, auth, assistant):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    gw = client.app.state.gateway
    try:
        msgs = say(ws, assistant, "Поставь таймер на 5 минут")
        assert reply_text(msgs) == "Таймер на пять минут запущен."
        sched = of_type(msgs, "schedule")[-1]
        assert len(sched["timers"]) == 1 and 295 <= sched["timers"][0]["end"] - datetime.now(UTC).timestamp() <= 301
        assert not [c for c in assistant.calls if c[0] == "llm"]                  # LLM не вызывался

        msgs = say(ws, assistant, "Сколько осталось?")
        assert reply_text(msgs).startswith("Осталось четыре минуты") or reply_text(msgs).startswith("Осталось пять минут")

        msgs = say(ws, assistant, "Поставь таймер на паузу")
        assert reply_text(msgs).startswith("Таймер на паузе") and of_type(msgs, "schedule")[-1]["timers"] == []
        msgs = say(ws, assistant, "Продолжи таймер")
        assert reply_text(msgs).startswith("Таймер продолжен") and len(of_type(msgs, "schedule")[-1]["timers"]) == 1

        # время вышло → цикл сервера озвучивает и ставит «звонит»
        fired = run_async(client, gw.scheduler.tick, datetime.now(UTC) + timedelta(seconds=310))
        assert len(fired) == 1 and sch.is_ringing(pid)
        from .helpers import read_until
        msgs = read_until(ws, reply_done)
        assert reply_text(msgs) == "Таймер завершён." and of_type(msgs, "schedule")[0]["timers"] == []
        assert run_async(client, gw.scheduler.tick, datetime.now(UTC) + timedelta(seconds=320)) == []   # второй раз — нет

        msgs = say(ws, assistant, "Стоп")
        assert reply_text(msgs) == "Выключаю." and {"type": "command", "name": "stop_ring"} in msgs
        assert not sch.is_ringing(pid)
        assert not [c for c in assistant.calls if c[0] == "llm"]
    finally:
        ctx.__exit__(None, None, None)


def test_device_reported_event_is_deduplicated_with_server_tick(client, auth, assistant):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    gw = client.app.state.gateway
    try:
        t = client.post(f"/api/v1/devices/{pid}/timers", json={"seconds": 30}, headers=auth).json()
        ws.receive_json()                                                     # schedule
        ws.send_json({"type": "sched_event", "kind": "timer", "id": t["id"]})  # плата сработала первой
        from .helpers import read_until
        msgs = read_until(ws, reply_done)
        assert reply_text(msgs) == "Таймер завершён."
        assert run_async(client, gw.scheduler.tick, datetime.now(UTC) + timedelta(seconds=60)) == []   # сервер уже знает
        ws.send_json({"type": "sched_event", "kind": "timer", "id": t["id"]})  # повтор от платы игнорируется
        ws.send_json({"type": "sched_event", "kind": "timer", "id": 99999})      # чужой/несуществующий — тоже
        ws.send_json({"type": "sched_event", "kind": "alarm", "id": "x"})
        ws.send_json({"type": "ping"})
        later = []
        while not later or later[-1]["type"] != "pong":
            later.append(ws.receive_json())
        assert [m["type"] for m in later] in (["pong"], ["time_sync", "pong"])   # соединение живо, лишнего голоса нет
    finally:
        ctx.__exit__(None, None, None)


def test_event_from_other_device_is_ignored(client, auth):
    p1, t1 = paired(client, auth)
    p2, t2 = paired(client, auth)
    tid = client.post(f"/api/v1/devices/{p1}/timers", json={"seconds": 30}, headers=auth).json()["id"]
    ctx, ws, _ = open_ws(client, p2, t2)
    try:
        ws.send_json({"type": "sched_event", "kind": "timer", "id": tid})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
    finally:
        ctx.__exit__(None, None, None)
    with SessionLocal() as db:
        assert db.get(Timer, tid).state == "running"


def test_alarm_rings_by_server_tick_with_label_and_time(client, auth, assistant):
    pid, token = paired(client, auth)
    client.post(f"/api/v1/devices/{pid}/alarms", json={"hour": 7, "minute": 30, "weekdays": [0, 1, 2, 3, 4],
                                                         "label": "работа"}, headers=auth)
    ctx, ws, _ = open_ws(client, pid, token)
    gw = client.app.state.gateway
    try:
        fired = run_async(client, gw.scheduler.tick, at("2026-10-16 07:30"))
        assert [f.kind for f in fired] == ["alarm"]
        from .helpers import read_until
        msgs = read_until(ws, reply_done)
        assert reply_text(msgs) == "Будильник «работа». Сейчас семь часов тридцать минут."
        assert registry.ringing[pid] > 0
    finally:
        ctx.__exit__(None, None, None)


def test_voice_alarms_set_list_cancel(client, auth, assistant):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        msgs = say(ws, assistant, "Разбуди меня в 7:30 по будням")
        assert reply_text(msgs).startswith("Будильник установлен на семь часов тридцать минут, по будням")
        assert of_type(msgs, "schedule")[-1]["alarms"][0]["wd"] == 0b11111
        msgs = say(ws, assistant, "Какие у меня будильники")
        assert reply_text(msgs) == "Будильников: один. семь часов тридцать минут, по будням."
        msgs = say(ws, assistant, "Поставь будильник")
        assert reply_text(msgs) == "На какое время поставить будильник?"
        msgs = say(ws, assistant, "Отмени будильник")
        assert reply_text(msgs) == "Будильник отменён." and of_type(msgs, "schedule")[-1]["alarms"] == []
        assert reply_text(say(ws, assistant, "Отмени будильник")) == "Будильников нет."
    finally:
        ctx.__exit__(None, None, None)
    with SessionLocal() as db:
        assert db.scalars(select(Alarm)).all() == []


@pytest.mark.parametrize("lang,phrase,expected", [
    ("ru", "Поставь таймер на 5 минут", "Таймер на пять минут запущен."),
    ("en", "Set a timer for 5 minutes", "Timer set for five minutes."),
    ("es", "Pon un temporizador de cinco minutos", "Temporizador de cinco minutos en marcha."),
    ("de", "Stelle einen Timer auf fünf Minuten", "Timer auf fünf Minuten gestellt."),
    ("fr", "Mets un minuteur de cinq minutes", "Minuteur de cinq minutes lancé."),
])
def test_voice_replies_in_device_language(client, auth, assistant, lang, phrase, expected):
    pid, token = paired(client, auth)
    client.patch(f"/api/v1/devices/{pid}/settings", json={"language": lang}, headers=auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        assert reply_text(say(ws, assistant, phrase)) == expected
        tts = [c[1] for c in assistant.calls if c[0] == "tts"]
        assert expected in tts                                            # именно эта фраза ушла в синтез речи
    finally:
        ctx.__exit__(None, None, None)


def test_anonymous_device_still_gets_llm_and_non_commands_go_to_llm(client, auth, assistant):
    # без привязки к аккаунту команды недоступны: обычный диалог
    with client.websocket_connect("/nils/v1/") as ws:
        ws.receive_json(); ws.receive_json()
        msgs = say(ws, assistant, "Поставь таймер на 5 минут")
        assert [c for c in assistant.calls if c[0] == "llm"]
    # привязанное устройство: обычный вопрос тоже уходит в LLM
    pid, token = paired(client, auth)
    assistant.calls.clear()
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        say(ws, assistant, "Почему небо голубое")
        assert [c for c in assistant.calls if c[0] == "llm"]
        assistant.calls.clear()
        say(ws, assistant, "Стоп")          # нечего останавливать — обычная реплика
        assert [c for c in assistant.calls if c[0] == "llm"]
    finally:
        ctx.__exit__(None, None, None)


def test_voice_time_date_volume(client, auth, assistant):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        assert reply_text(say(ws, assistant, "Который час")).startswith("Сейчас ")
        assert reply_text(say(ws, assistant, "Какое сегодня число")).startswith("Сегодня ")
        msgs = say(ws, assistant, "Громкость на 40")
        assert reply_text(msgs) == "Громкость сорок." and of_type(msgs, "config")[-1]["volume"] == 40
        msgs = say(ws, assistant, "Громче")
        assert of_type(msgs, "config")[-1]["volume"] == 50 and reply_text(msgs) == "Громкость пятьдесят."
        assert of_type(say(ws, assistant, "Тише"), "config")[-1]["volume"] == 40
        assert not [c for c in assistant.calls if c[0] == "llm"]
    finally:
        ctx.__exit__(None, None, None)
    assert client.get(f"/api/v1/devices/{pid}/settings", headers=auth).json()["volume"] == 40


# ------------------------------------------------------------------ навыки с фиксированным временем
def run_skill(db, device, text, lang="ru", now=NOW):
    intent = parse(text, lang)
    assert intent is not None, text
    ctx = skills.Context(db, device, {"language": lang, "timezone": device.settings.timezone}, now=now)
    return asyncio.run(skills.run(intent, ctx))


def test_time_and_date_phrases_per_language(dev):
    db, d = dev
    now = at("2026-10-16 22:35")
    expect = {
        "ru": ("Который час", "Сейчас двадцать два часа тридцать пять минут."),
        "en": ("What time is it", "It's ten thirty five in the evening."),
        "es": ("Qué hora es", "Son las diez y treinta y cinco de la noche."),
        "de": ("Wie spät ist es", "Es ist zweiundzwanzig Uhr fünfunddreißig."),
        "fr": ("Quelle heure est-il", "Il est vingt-deux heures trente-cinq."),
    }
    for lang, (q, a) in expect.items():
        assert run_skill(db, d, q, lang, now).text == a
    dates = {"ru": ("Какое сегодня число", "Сегодня пятница, шестнадцатое октября."),
             "en": ("What's the date today", "Today is Friday, October sixteenth."),
             "es": ("Qué día es hoy", "Hoy es viernes, dieciséis de octubre."),
             "de": ("Welcher Tag ist heute", "Heute ist Freitag, der sechzehnte Oktober."),
             "fr": ("Quel jour sommes-nous", "Nous sommes vendredi seize octobre.")}
    for lang, (q, a) in dates.items():
        assert run_skill(db, d, q, lang, now).text == a


def test_alarm_time_choice_without_am_pm(dev):
    db, d = dev                                                # сейчас пятница 12:00
    r = run_skill(db, d, "Разбуди меня в 7:30")
    a = sch.alarm_list(db, d)[-1]
    assert (a.hour, a.minute, a.one_shot_date) == (19, 30, "2026-10-16") and r.text.endswith(", сегодня.")
    run_skill(db, d, "Поставь будильник на 6 утра")
    assert [(x.hour, x.one_shot_date) for x in sch.alarm_list(db, d) if x.hour == 6] == [(6, "2026-10-17")]
    r = run_skill(db, d, "Разбуди меня через 20 минут")
    rel = [x for x in sch.alarm_list(db, d) if (x.hour, x.minute) == (12, 20)]
    assert rel and rel[0].one_shot_date == "2026-10-16"
    r = run_skill(db, d, "Разбуди меня завтра в восемь вечера")
    assert any((x.hour, x.minute, x.one_shot_date) == (20, 0, "2026-10-17") for x in sch.alarm_list(db, d)) and r.text.endswith(", завтра.")
    r = run_skill(db, d, "Разбуди меня в понедельник в 7")
    assert any((x.hour, x.one_shot_date) == (7, "2026-10-19") for x in sch.alarm_list(db, d)) and "понедельник" in r.text


def test_alarm_cancel_by_time_and_ambiguity(dev):
    db, d = dev
    run_skill(db, d, "Поставь будильник на 6 утра")
    run_skill(db, d, "Поставь будильник на 8 утра")
    assert run_skill(db, d, "Отмени будильник").text.startswith("Какой будильник")
    assert run_skill(db, d, "Отмени будильник на 6 утра").text == "Будильник отменён."
    assert [a.hour for a in sch.alarm_list(db, d)] == [8]
    assert run_skill(db, d, "Отмени будильник на 9 вечера").text == "Будильников нет."
    run_skill(db, d, "Поставь будильник на 9 утра")
    assert run_skill(db, d, "Удали все будильники").text == "Все будильники отменены."
    assert sch.alarm_list(db, d) == []


def test_timer_cancel_by_duration_and_all(dev):
    db, d = dev
    run_skill(db, d, "Поставь таймер на 5 минут")
    run_skill(db, d, "Поставь таймер на 10 минут")
    assert run_skill(db, d, "Отмени таймер").text.startswith("Таймеров несколько")
    assert run_skill(db, d, "Отмени таймер на 5 минут").text == "Таймер отменён."
    assert [t.duration_sec for t in sch.active_timers(db, d)] == [600]
    assert run_skill(db, d, "Сколько осталось").text in ("Осталось десять минут.", "Осталось девять минут пятьдесят девять секунд.")
    run_skill(db, d, "Поставь таймер на 3 минуты")
    assert run_skill(db, d, "Сколько осталось").text.startswith("Таймеров: два.")
    assert run_skill(db, d, "Отмени все таймеры").text == "Все таймеры отменены."
    assert run_skill(db, d, "Отмени таймер").text == "Активных таймеров нет."
    assert run_skill(db, d, "Поставь таймер").text == "На сколько поставить таймер?"
    assert run_skill(db, d, "Поставь таймер на 30 часов").text.startswith("Таймер может быть не длиннее")


def test_time_sync_offset_follows_daylight_saving():
    summer, winter = at("2026-07-01 12:00", "UTC"), at("2026-01-15 12:00", "UTC")
    assert sch.time_sync_message("America/New_York", summer)["utc_offset"] == -4 * 3600
    assert sch.time_sync_message("America/New_York", winter)["utc_offset"] == -5 * 3600
    assert sch.time_sync_message("Europe/Berlin", summer)["utc_offset"] == 2 * 3600
    assert sch.time_sync_message("Europe/Berlin", winter)["utc_offset"] == 3600
    assert sch.time_sync_message("Asia/Kolkata", summer)["utc_offset"] == 19800                  # поясы с получасом


def test_scheduler_pushes_time_sync_to_online_devices_hourly(client, auth):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    gw = client.app.state.gateway
    try:
        from .helpers import receive
        run_async(client, gw.scheduler.tick, NOW)                                    # первый проход рассылает время
        assert receive(ws)["type"] == "time_sync"
        run_async(client, gw.scheduler.tick, NOW)                                    # в течение часа — не повторяет
        ws.send_json({"type": "ping"})
        assert receive(ws)["type"] == "pong"
        gw.scheduler._last_sync -= sch.TIME_SYNC_PERIOD_SEC + 1                      # прошёл час
        run_async(client, gw.scheduler.tick, NOW)
        assert receive(ws)["type"] == "time_sync"
    finally:
        ctx.__exit__(None, None, None)
