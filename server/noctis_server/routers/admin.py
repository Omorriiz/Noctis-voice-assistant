"""Административный API: пользователи, колонки, сводка, журнал действий.

Пароли не показываются: хранятся только хэши. Вместо просмотра есть «сброс пароля» (одноразовый временный пароль).
Содержимое диалогов администратору не отдаётся (приватность) — только счётчики.
Все изменяющие действия пишутся в журнал audit_log.
"""
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import registry
from .. import privacy
from ..db import get_db
from ..deps import require_admin
from ..models import (Alarm, AuditLog, AuthSession, Device, HistoryEntry, MediaTrack, Timer, User, UserDevice)
from ..security import hash_password

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class AdminDevice(BaseModel):
    public_id: str
    name: str
    online: bool
    enabled: bool
    firmware_version: str
    last_seen: datetime | None
    last_ip: str
    language: str = ""
    wake_word: str = ""
    timers: int = 0
    alarms: int = 0
    history_entries: int = 0


class AdminUser(BaseModel):
    id: int
    email: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    consent_at: datetime | None
    devices: int
    online_devices: int


class AdminUserDetail(AdminUser):
    language: str = ""
    history_enabled: bool = True
    tracks: int = 0
    speakers: list[AdminDevice] = []


class TempPassword(BaseModel):
    temporary_password: str


def _audit(db: Session, admin: User, request: Request, action: str, target: str = "") -> None:
    db.add(AuditLog(admin_id=admin.id, admin_email=admin.email, action=action, target=target[:254],
                    ip=request.client.host if request.client else ""))


def _user_row(db: Session, u: User) -> AdminUser:
    pubs = db.scalars(select(Device.public_id).join(UserDevice, UserDevice.device_id == Device.id)
                      .where(UserDevice.user_id == u.id)).all()
    return AdminUser(id=u.id, email=u.email, display_name=u.display_name, role=u.role.name, is_active=u.is_active,
                     created_at=u.created_at, last_login_at=u.last_login_at, consent_at=u.consent_at,
                     devices=len(pubs), online_devices=sum(p in registry.online_devices for p in pubs))


def _device_row(db: Session, d: Device) -> AdminDevice:
    count = lambda model: db.scalar(select(func.count()).select_from(model).where(model.device_id == d.id)) or 0  # noqa: E731
    return AdminDevice(public_id=d.public_id, name=d.name, online=d.public_id in registry.online_devices,
                       enabled=d.enabled, firmware_version=d.firmware_version, last_seen=d.last_seen,
                       last_ip=d.last_ip, language=d.settings.language if d.settings else "",
                       wake_word=d.settings.wake_word if d.settings else "", timers=count(Timer),
                       alarms=count(Alarm), history_entries=count(HistoryEntry))


def _target_user(db: Session, admin: User, user_id: int, *, allow_admin: bool = False) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if not allow_admin and (user.id == admin.id or user.role.name == "admin"):
        raise HTTPException(403, "Cannot change an administrator account here")
    return user


@router.get("/stats")
def stats(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    n = lambda model, *where: db.scalar(select(func.count()).select_from(model).where(*where)) or 0  # noqa: E731
    return {"users": n(User), "active_users": n(User, User.is_active.is_(True)), "speakers": n(Device),
            "speakers_online": len(registry.online_devices), "timers": n(Timer), "alarms": n(Alarm),
            "tracks": n(MediaTrack), "history_entries": n(HistoryEntry)}


@router.get("/users", response_model=list[AdminUser])
def list_users(q: str = Query("", max_length=80), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               _: User = Depends(require_admin), db: Session = Depends(get_db)):
    stmt = select(User).order_by(User.id.desc()).limit(limit).offset(offset)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.display_name).like(like)))
    return [_user_row(db, u) for u in db.scalars(stmt)]


@router.get("/users/{user_id}", response_model=AdminUserDetail)
def user_detail(user_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    devices = db.scalars(select(Device).join(UserDevice, UserDevice.device_id == Device.id)
                         .where(UserDevice.user_id == u.id)).all()
    row = _user_row(db, u)
    return AdminUserDetail(**row.model_dump(), language=u.settings.language if u.settings else "",
                           history_enabled=u.settings.history_enabled if u.settings else True,
                           tracks=db.scalar(select(func.count()).select_from(MediaTrack).where(MediaTrack.owner_id == u.id)) or 0,
                           speakers=[_device_row(db, d) for d in devices])


@router.post("/users/{user_id}/disable", status_code=204)
def disable_user(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _target_user(db, admin, user_id)
    user.is_active = False
    for sess in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
        sess.revoked = True
    _audit(db, admin, request, "user_disable", user.email)
    db.commit()


@router.post("/users/{user_id}/enable", status_code=204)
def enable_user(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _target_user(db, admin, user_id)
    user.is_active = True
    _audit(db, admin, request, "user_enable", user.email)
    db.commit()


@router.post("/users/{user_id}/reset-password", response_model=TempPassword)
def reset_password(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Задаёт одноразовый временный пароль и завершает все сессии пользователя. Показывается один раз."""
    user = _target_user(db, admin, user_id)
    temp = secrets.token_urlsafe(9)
    user.password_hash = hash_password(temp)
    for sess in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
        sess.revoked = True
    _audit(db, admin, request, "user_reset_password", user.email)
    db.commit()
    return TempPassword(temporary_password=temp)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _target_user(db, admin, user_id)
    _audit(db, admin, request, "user_delete", user.email)
    db.commit()
    await privacy.erase_user(db, user)


@router.delete("/devices/{public_id}", status_code=204)
def delete_device(public_id: str, request: Request, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    device = db.scalar(select(Device).where(Device.public_id == public_id))
    if device is None:
        raise HTTPException(404, "Device not found")
    _audit(db, admin, request, "device_delete", f"{device.name} ({device.public_id})")
    db.delete(device)
    db.commit()


@router.get("/audit")
def audit(limit: int = Query(50, ge=1, le=200), _: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return [{"id": r.id, "at": r.created_at, "admin": r.admin_email, "action": r.action, "target": r.target,
             "ip": r.ip} for r in rows]
