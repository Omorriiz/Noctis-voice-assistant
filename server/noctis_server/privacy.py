"""Персональные данные пользователя (этап 8): выгрузка («мои данные»), очистка истории, полное стирание.

Всё выполняется только по запросу самого пользователя из личного кабинета (приложение, сайт); автоматических чисток по расписанию нет.
Секреты в выгрузку не входят: ни хэш пароля, ни хэши токенов устройств и сессий.
"""
import shutil
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import registry
from .media import library
from .models import Alarm, AuthSession, Device, HistoryEntry, MediaTrack, Playlist, Timer, User, UserDevice


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def owned_devices(db: Session, user: User) -> list[Device]:
    return list(db.scalars(select(Device).join(UserDevice, UserDevice.device_id == Device.id).where(UserDevice.user_id == user.id)))


def export_user(db: Session, user: User) -> dict:
    """Всё, что сервер хранит о пользователе, в читаемом виде (без секретов)."""
    s = user.settings
    data = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": {"email": user.email, "display_name": user.display_name, "role": user.role.name, "active": user.is_active,
                    "created_at": _iso(user.created_at), "consent_at": _iso(user.consent_at), "last_login_at": _iso(user.last_login_at)},
        "settings": None if s is None else {k: getattr(s, k) for k in ("wake_word", "voice_gender", "character_id", "language", "theme",
                                                                       "history_enabled", "data_collection")},
        "sessions": [{"created_at": _iso(x.created_at), "expires_at": _iso(x.expires_at), "revoked": x.revoked, "user_agent": x.user_agent}
                     for x in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id).order_by(AuthSession.id))],
        "devices": [],
        "library": [{"title": t.title, "artist": t.artist, "album": t.album, "genre": t.genre, "source": t.source,
                     "duration_sec": t.duration_sec, "license": t.license,
                     "location": t.uri if t.source != "local" else "(файл в вашей библиотеке на сервере)"}
                    for t in db.scalars(select(MediaTrack).where(MediaTrack.owner_id == user.id).order_by(MediaTrack.id))],
        "playlists": [{"name": p.name, "tracks": [db.get(MediaTrack, i.media_id).title for i in p.items if db.get(MediaTrack, i.media_id)]}
                      for p in db.scalars(select(Playlist).where(Playlist.owner_id == user.id).order_by(Playlist.id))],
        "note": "Хэш пароля и токены устройств в выгрузку не входят: они не позволяют восстановить секрет и хранятся только в виде хэшей.",
    }
    for d in owned_devices(db, user):
        st = d.settings
        data["devices"].append({
            "id": d.public_id, "name": d.name, "firmware_version": d.firmware_version, "created_at": _iso(d.created_at),
            "last_seen": _iso(d.last_seen), "last_ip": d.last_ip, "enabled": d.enabled,
            "settings": None if st is None else {k: getattr(st, k) for k in ("wake_word", "voice_gender", "character_id", "language", "volume",
                                                                              "timezone", "home_city")},
            "timers": [{"label": t.label, "duration_sec": t.duration_sec, "state": t.state, "created_at": _iso(t.created_at)}
                       for t in db.scalars(select(Timer).where(Timer.device_id == d.id).order_by(Timer.id))],
            "alarms": [{"label": a.label, "time": f"{a.hour:02d}:{a.minute:02d}", "weekdays": a.weekdays, "date": a.one_shot_date,
                        "melody": a.melody, "enabled": a.enabled}
                       for a in db.scalars(select(Alarm).where(Alarm.device_id == d.id).order_by(Alarm.id))],
            "history": [{"at": _iso(h.created_at), "you_said": h.user_text, "assistant": h.assistant_text, "language": h.language}
                        for h in db.scalars(select(HistoryEntry).where(HistoryEntry.device_id == d.id).order_by(HistoryEntry.id))],
        })
    return data


def clear_history(db: Session, user: User) -> int:
    """Удаляет историю диалогов всех колонок пользователя. Возвращает число удалённых записей."""
    ids = [d.id for d in owned_devices(db, user)]
    if not ids:
        return 0
    n = db.execute(delete(HistoryEntry).where(HistoryEntry.device_id.in_(ids))).rowcount
    db.commit()
    return n or 0


async def erase_user(db: Session, user: User) -> list[str]:
    """Стирает пользователя полностью: учётную запись, настройки, сессии, медиатеку (в том числе загруженные файлы), плейлисты и его колонки
    с историей, таймерами и будильниками (если у колонки нет других владельцев). Включённые колонки получают сброс к заводским настройкам и
    возвращаются в режим сопряжения. Возвращает идентификаторы удалённых колонок."""
    removed = []
    for dev in owned_devices(db, user):
        others = db.scalar(select(UserDevice.id).where(UserDevice.device_id == dev.id, UserDevice.user_id != user.id).limit(1))
        if others is None:
            conn = registry.online_devices.get(dev.public_id)
            if conn is not None:
                await conn.send_json({"type": "command", "name": "factory_reset"})
            removed.append(dev.public_id)
            db.delete(dev)
    shutil.rmtree(library.user_dir(user.id), ignore_errors=True)
    db.delete(user)
    db.commit()
    return removed
