"""Таймеры и будильники: хранение в БД, расчёт срабатывания, снимок расписания для платы и сервисный цикл.

Источник истины — БД сервера. Плата получает полный снимок (`schedule`) при подключении и при каждом изменении,
хранит его в NVS и сама срабатывает по времени (будильник звонит и без связи с сервером). Сервер срабатывает
параллельно и добавляет голосовое уведомление; повторные срабатывания гасятся (состояние таймера, `last_fired_key`).
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import registry
from .db import SessionLocal
from .models import Alarm, Device, Timer, utcnow
from .timeutil import local_now, utc_offset_sec, zone

log = logging.getLogger("noctis.scheduler")

MAX_TIMERS = 8                 # как SCHED_MAX_TIMERS в прошивке
MAX_ALARMS = 16                # как SCHED_MAX_ALARMS
MAX_TIMER_SEC = 24 * 3600
RING_SECONDS = 60              # столько звонит плата, если её не остановили
MELODIES = ("default", "soft", "rooster", "beeps")
TICK_SEC = 1.0
TIME_SYNC_PERIOD_SEC = 3600


class ScheduleError(ValueError):
    """Ошибка пользователя (лимиты, неверные значения); сообщение — код для ответа/HTTP 422."""


def aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ таймеры
def active_timers(db: Session, device: Device) -> list[Timer]:
    return list(db.scalars(select(Timer).where(Timer.device_id == device.id, Timer.state.in_(("running", "paused")))
                           .order_by(Timer.id)))


def remaining_sec(timer: Timer, now: datetime | None = None) -> int:
    if timer.state == "running" and timer.ends_at:
        return max(0, int((aware(timer.ends_at) - (now or utcnow())).total_seconds() + 0.999))
    return timer.remaining_sec


def create_timer(db: Session, device: Device, seconds: int, label: str = "", now: datetime | None = None) -> Timer:
    if not 1 <= seconds <= MAX_TIMER_SEC:
        raise ScheduleError("duration")
    if len(active_timers(db, device)) >= MAX_TIMERS:
        raise ScheduleError("limit")
    now = now or utcnow()
    timer = Timer(device_id=device.id, label=label[:80], duration_sec=seconds, remaining_sec=seconds,
                  state="running", ends_at=now + timedelta(seconds=seconds))
    db.add(timer)
    db.commit()
    return timer


def pause_timer(db: Session, timer: Timer, now: datetime | None = None) -> bool:
    if timer.state != "running":
        return False
    timer.remaining_sec = remaining_sec(timer, now)
    timer.state, timer.ends_at = "paused", None
    db.commit()
    return True


def resume_timer(db: Session, timer: Timer, now: datetime | None = None) -> bool:
    if timer.state != "paused":
        return False
    timer.state, timer.ends_at = "running", (now or utcnow()) + timedelta(seconds=timer.remaining_sec)
    db.commit()
    return True


def cancel_timer(db: Session, timer: Timer) -> bool:
    if timer.state not in ("running", "paused"):
        return False
    timer.state, timer.ends_at = "cancelled", None
    db.commit()
    return True


# ------------------------------------------------------------------ будильники
def weekday_mask(days: list[int]) -> int:
    return sum(1 << d for d in set(days) if 0 <= d <= 6)


def alarm_list(db: Session, device: Device) -> list[Alarm]:
    return list(db.scalars(select(Alarm).where(Alarm.device_id == device.id).order_by(Alarm.hour, Alarm.minute, Alarm.id)))


def next_occurrence(hour: int, minute: int, weekdays: list[int], tz_name: str, now_utc: datetime | None = None,
                    day_offset: int = 0) -> datetime:
    """Ближайшее местное время срабатывания (aware) не раньше «сейчас»."""
    now = local_now(tz_name, now_utc)
    base = now.date() + timedelta(days=day_offset)
    for add in range(0, 9):
        d = base + timedelta(days=add)
        cand = datetime(d.year, d.month, d.day, hour, minute, tzinfo=zone(tz_name))
        if cand <= now and add == 0 and day_offset == 0:
            continue
        if cand <= now:
            continue
        if weekdays and d.weekday() not in weekdays:
            continue
        return cand
    raise ScheduleError("time")


def create_alarm(db: Session, device: Device, hour: int, minute: int, weekdays: list[int] | None = None,
                 label: str = "", melody: str = "default", day_offset: int = 0,
                 now: datetime | None = None, one_shot_date: str | None = None) -> Alarm:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError("time")
    if melody not in MELODIES:
        raise ScheduleError("melody")
    weekdays = sorted(set(weekdays or []))
    if any(not 0 <= d <= 6 for d in weekdays):
        raise ScheduleError("weekdays")
    if len(alarm_list(db, device)) >= MAX_ALARMS:
        raise ScheduleError("limit")
    date_str = None
    if not weekdays:
        if one_shot_date:
            try:
                date.fromisoformat(one_shot_date)
            except ValueError:
                raise ScheduleError("date")
            date_str = one_shot_date
        else:
            date_str = next_occurrence(hour, minute, [], device.settings.timezone, now, day_offset).date().isoformat()
    alarm = Alarm(device_id=device.id, label=label[:80], hour=hour, minute=minute, weekdays=weekdays,
                  one_shot_date=date_str, melody=melody, enabled=True)
    db.add(alarm)
    db.commit()
    return alarm


def alarm_due_key(alarm: Alarm, local: datetime) -> str | None:
    """Ключ минуты, если будильник должен звонить в это местное время (иначе None)."""
    if not alarm.enabled or alarm.hour != local.hour or alarm.minute != local.minute:
        return None
    if alarm.weekdays:
        if local.weekday() not in alarm.weekdays:
            return None
    elif alarm.one_shot_date != local.date().isoformat():
        return None
    key = local.strftime("%Y-%m-%d %H:%M")
    return None if alarm.last_fired_key == key else key


# ------------------------------------------------------------------ срабатывание
@dataclass
class Fired:
    kind: str          # "timer" | "alarm"
    device_pk: int
    public_id: str
    item_id: int
    label: str
    hour: int = 0
    minute: int = 0
    settings: dict | None = None


def _settings_dict(device: Device) -> dict:
    s = device.settings
    return {"language": s.language, "voice_gender": s.voice_gender, "character_id": s.character_id,
            "timezone": s.timezone, "volume": s.volume}


def fire_timer(db: Session, timer: Timer, now: datetime | None = None) -> Fired | None:
    """Помечает таймер завершённым; None, если он уже завершён/отменён (повтор от платы или цикла)."""
    if timer.state not in ("running", "paused"):
        return None
    timer.state, timer.ends_at, timer.remaining_sec, timer.finished_at = "finished", None, 0, now or utcnow()
    db.commit()
    device = db.get(Device, timer.device_id)
    return Fired("timer", device.id, device.public_id, timer.id, timer.label, settings=_settings_dict(device))


def fire_alarm(db: Session, alarm: Alarm, now: datetime | None = None) -> Fired | None:
    device = db.get(Device, alarm.device_id)
    local = local_now(device.settings.timezone, now)
    key = alarm_due_key(alarm, local)
    if key is None:
        return None
    alarm.last_fired_key = key
    if not alarm.weekdays:
        alarm.enabled = False          # однократный отработал
    db.commit()
    return Fired("alarm", device.id, device.public_id, alarm.id, alarm.label, alarm.hour, alarm.minute,
                 _settings_dict(device))


def collect_due(db: Session, now: datetime | None = None) -> list[Fired]:
    now = now or utcnow()
    fired: list[Fired] = []
    for timer in db.scalars(select(Timer).where(Timer.state == "running", Timer.ends_at <= now)).all():
        if (f := fire_timer(db, timer, now)):
            fired.append(f)
    for alarm in db.scalars(select(Alarm).where(Alarm.enabled.is_(True))).all():
        if (f := fire_alarm(db, alarm, now)):
            fired.append(f)
    return fired


def handle_device_event(db: Session, device: Device, kind: str, item_id: int, now: datetime | None = None) -> Fired | None:
    """Плата сообщила, что у неё сработал таймер/будильник (она опережает сервер на доли секунды)."""
    if kind == "timer":
        timer = db.get(Timer, item_id)
        if timer and timer.device_id == device.id:
            return fire_timer(db, timer, now)
    elif kind == "alarm":
        alarm = db.get(Alarm, item_id)
        if alarm and alarm.device_id == device.id:
            return fire_alarm(db, alarm, now)
    return None


# ------------------------------------------------------------------ сообщения для платы
def schedule_snapshot(db: Session, device: Device) -> dict:
    """Сообщение `schedule`: только то, что плата должна отработать сама."""
    timers = [{"id": t.id, "end": int(aware(t.ends_at).timestamp()), "label": t.label[:23]}
              for t in active_timers(db, device) if t.state == "running" and t.ends_at]
    alarms = []
    for a in alarm_list(db, device):
        if not a.enabled:
            continue
        ymd = int(a.one_shot_date.replace("-", "")) if (not a.weekdays and a.one_shot_date) else 0
        alarms.append({"id": a.id, "h": a.hour, "m": a.minute, "wd": weekday_mask(a.weekdays), "date": ymd,
                       "label": a.label[:23], "melody": a.melody})
    return {"type": "schedule", "rev": int(time.time()), "timers": timers[:MAX_TIMERS], "alarms": alarms[:MAX_ALARMS]}


def time_sync_message(tz_name: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {"type": "time_sync", "epoch": int(now.timestamp()), "utc_offset": utc_offset_sec(tz_name, now)}


async def push_schedule(conn) -> None:
    if conn is None or conn.device_id is None:
        return
    with SessionLocal() as db:
        device = db.get(Device, conn.device_id)
        if device:
            await conn.send_json(schedule_snapshot(db, device))


async def push_time_sync(conn) -> None:
    if conn is not None:
        await conn.send_json(time_sync_message(conn.settings.get("timezone")))


async def push_schedule_for(public_id: str) -> None:
    """После изменения через REST/голос: отправить снимок, если плата онлайн."""
    await push_schedule(registry.online_devices.get(public_id))


# ------------------------------------------------------------------ сервисный цикл
class SchedulerService:
    """Раз в секунду проверяет БД, озвучивает срабатывания и раз в час шлёт платам время."""

    def __init__(self, gateway, notify):
        self.gateway = gateway
        self.notify = notify          # async (conn, Fired) -> None: фраза на языке устройства
        self.task: asyncio.Task | None = None
        self._last_sync = 0.0

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ошибка цикла планировщика")
            await asyncio.sleep(TICK_SEC)

    async def tick(self, now: datetime | None = None) -> list[Fired]:
        with SessionLocal() as db:
            fired = collect_due(db, now)
        for f in fired:
            await self.deliver(f)
        mono = time.monotonic()
        if mono - self._last_sync >= TIME_SYNC_PERIOD_SEC:
            self._last_sync = mono
            for conn in list(registry.online_devices.values()):
                await push_time_sync(conn)
        return fired

    async def deliver(self, f: Fired) -> None:
        registry.ringing[f.public_id] = time.time() + RING_SECONDS
        conn = registry.online_devices.get(f.public_id)
        if conn is None:
            return                     # плата офлайн — зазвонит сама (если расписание уже у неё)
        await push_schedule(conn)      # убрать сработавшее из снимка
        try:
            await self.notify(conn, f)
        except Exception:
            log.exception("не удалось озвучить срабатывание")


def is_ringing(public_id: str) -> bool:
    until = registry.ringing.get(public_id)
    if until and until > time.time():
        return True
    registry.ringing.pop(public_id, None)
    return False


def stop_ringing(public_id: str) -> bool:
    return registry.ringing.pop(public_id, None) is not None
