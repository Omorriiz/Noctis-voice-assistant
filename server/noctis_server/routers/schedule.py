"""REST-API таймеров и будильников устройства (для приложения; голосовые команды — skills.py)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import scheduler as sch
from ..db import get_db
from ..deps import owned_device
from ..models import Alarm, Device, Timer
from ..timeutil import local_now, utc_offset_sec

router = APIRouter(prefix="/api/v1/devices/{public_id}", tags=["schedule"])


class TimerCreate(BaseModel):
    seconds: int = Field(ge=1, le=sch.MAX_TIMER_SEC)
    label: str = Field(default="", max_length=80)


class TimerOut(BaseModel):
    id: int
    label: str
    duration_sec: int
    remaining_sec: int
    state: str


class AlarmCreate(BaseModel):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    weekdays: list[int] = Field(default_factory=list, max_length=7)   # 0 = понедельник; пусто = однократно
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    label: str = Field(default="", max_length=80)
    melody: str = "default"


class AlarmPatch(BaseModel):
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    weekdays: list[int] | None = Field(default=None, max_length=7)
    label: str | None = Field(default=None, max_length=80)
    melody: str | None = None
    enabled: bool | None = None


class AlarmOut(BaseModel):
    id: int
    hour: int
    minute: int
    weekdays: list[int]
    date: str | None
    label: str
    melody: str
    enabled: bool


def timer_out(t: Timer) -> TimerOut:
    return TimerOut(id=t.id, label=t.label, duration_sec=t.duration_sec, remaining_sec=sch.remaining_sec(t), state=t.state)


def alarm_out(a: Alarm) -> AlarmOut:
    return AlarmOut(id=a.id, hour=a.hour, minute=a.minute, weekdays=list(a.weekdays or []), date=a.one_shot_date,
                    label=a.label, melody=a.melody, enabled=a.enabled)


def _timer(db: Session, device: Device, timer_id: int) -> Timer:
    t = db.get(Timer, timer_id)
    if t is None or t.device_id != device.id:
        raise HTTPException(404, "Timer not found")
    return t


def _alarm(db: Session, device: Device, alarm_id: int) -> Alarm:
    a = db.get(Alarm, alarm_id)
    if a is None or a.device_id != device.id:
        raise HTTPException(404, "Alarm not found")
    return a


def _fail(e: sch.ScheduleError):
    code = 409 if str(e) == "limit" else 422
    raise HTTPException(code, f"Invalid schedule: {e}")


@router.get("/time")
def device_time(device: Device = Depends(owned_device)):
    tz = device.settings.timezone
    now = local_now(tz)
    return {"timezone": tz, "local_time": now.isoformat(timespec="seconds"), "utc_offset": utc_offset_sec(tz)}


@router.get("/timers", response_model=list[TimerOut])
def list_timers(device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    return [timer_out(t) for t in sch.active_timers(db, device)]


@router.post("/timers", response_model=TimerOut, status_code=201)
async def add_timer(body: TimerCreate, device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    try:
        timer = sch.create_timer(db, device, body.seconds, body.label)
    except sch.ScheduleError as e:
        _fail(e)
    await sch.push_schedule_for(device.public_id)
    return timer_out(timer)


@router.post("/timers/{timer_id}/{action}", response_model=TimerOut)
async def timer_action(timer_id: int, action: str, device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    if action not in ("pause", "resume"):
        raise HTTPException(404, "Unknown action")
    timer = _timer(db, device, timer_id)
    if not (sch.pause_timer if action == "pause" else sch.resume_timer)(db, timer):
        raise HTTPException(409, f"Timer is {timer.state}")
    await sch.push_schedule_for(device.public_id)
    return timer_out(timer)


@router.delete("/timers/{timer_id}", status_code=204)
async def delete_timer(timer_id: int, device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    timer = _timer(db, device, timer_id)
    sch.cancel_timer(db, timer)
    await sch.push_schedule_for(device.public_id)


@router.get("/alarms", response_model=list[AlarmOut])
def list_alarms(device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    return [alarm_out(a) for a in sch.alarm_list(db, device)]


@router.post("/alarms", response_model=AlarmOut, status_code=201)
async def add_alarm(body: AlarmCreate, device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    try:
        alarm = sch.create_alarm(db, device, body.hour, body.minute, body.weekdays, body.label, body.melody,
                                 one_shot_date=body.date)
    except sch.ScheduleError as e:
        _fail(e)
    await sch.push_schedule_for(device.public_id)
    return alarm_out(alarm)


@router.patch("/alarms/{alarm_id}", response_model=AlarmOut)
async def patch_alarm(alarm_id: int, body: AlarmPatch, device: Device = Depends(owned_device),
                      db: Session = Depends(get_db)):
    alarm = _alarm(db, device, alarm_id)
    changes = body.model_dump(exclude_none=True)
    if "melody" in changes and changes["melody"] not in sch.MELODIES:
        raise HTTPException(422, "Invalid schedule: melody")
    if "weekdays" in changes:
        days = sorted(set(changes["weekdays"]))
        if any(not 0 <= d <= 6 for d in days):
            raise HTTPException(422, "Invalid schedule: weekdays")
        changes["weekdays"] = days
    for key, value in changes.items():
        setattr(alarm, key, value)
    if not alarm.weekdays and (changes.keys() & {"hour", "minute", "weekdays"} or
                               (changes.get("enabled") and not alarm.one_shot_date)):
        # однократный будильник после изменения времени/включения — на ближайшее наступление
        alarm.one_shot_date = sch.next_occurrence(alarm.hour, alarm.minute, [], device.settings.timezone).date().isoformat()
        alarm.last_fired_key = ""
    db.commit()
    await sch.push_schedule_for(device.public_id)
    return alarm_out(alarm)


@router.delete("/alarms/{alarm_id}", status_code=204)
async def delete_alarm(alarm_id: int, device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    db.delete(_alarm(db, device, alarm_id))
    db.commit()
    await sch.push_schedule_for(device.public_id)
