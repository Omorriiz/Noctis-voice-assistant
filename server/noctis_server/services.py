"""Общая бизнес-логика, которую используют и REST, и WebSocket."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import registry
from .catalog import safe_wake_word
from .config import settings
from .models import (AuthSession, Device, DeviceSettings, HistoryEntry, Role, User,
                     UserDevice, UserSettings, WakeModel, utcnow)
from .schemas import DeviceOut, DeviceSettingsOut
from .security import create_access_token, new_token, sha256

log = logging.getLogger("noctis.services")


def seed_roles(db: Session) -> None:
    for name in ("user", "admin"):
        if db.scalar(select(Role).where(Role.name == name)) is None:
            db.add(Role(name=name))
    db.commit()


def get_role(db: Session, name: str) -> Role:
    return db.scalar(select(Role).where(Role.name == name))


def issue_tokens(db: Session, user: User, user_agent: str = "") -> dict:
    refresh = new_token()
    db.add(AuthSession(user_id=user.id, refresh_hash=sha256(refresh), user_agent=user_agent[:200],
                       expires_at=utcnow() + timedelta(days=settings.refresh_token_days)))
    db.commit()
    return {"access_token": create_access_token(user.id, user.role.name), "refresh_token": refresh,
            "expires_in": settings.access_token_minutes * 60}


def as_aware(dt: datetime) -> datetime:
    """SQLite возвращает naive datetime; приводим к UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def device_out(device: Device) -> DeviceOut:
    out = DeviceOut.model_validate(device)
    out.online = device.public_id in registry.online_devices
    return out


def create_device(db: Session, user: User, name: str, tz: str) -> tuple[Device, str]:
    """Создаёт устройство с настройками по умолчанию из профиля пользователя.
    Возвращает (устройство, секрет)."""
    secret = new_token()
    device = Device(public_id=new_token()[:12].replace("-", "x").replace("_", "y"),
                    token_hash=sha256(secret), name=name)
    us = user.settings
    device.settings = DeviceSettings(wake_word=us.wake_word, voice_gender=us.voice_gender,
                                     character_id=us.character_id, language=us.language, timezone=tz)
    db.add(device)
    db.flush()
    db.add(UserDevice(user_id=user.id, device_id=device.id, is_owner=True))
    db.commit()
    return device, secret


def save_history(db: Session, device: Device, user_text: str, answer: str, timing: dict) -> None:
    """Пишет реплику в историю, только если хотя бы один владелец не отключил историю."""
    owners = db.scalars(select(UserSettings).join(UserDevice, UserDevice.user_id == UserSettings.user_id)
                        .where(UserDevice.device_id == device.id)).all()
    if owners and not any(o.history_enabled for o in owners):
        return
    db.add(HistoryEntry(device_id=device.id, user_text=user_text, assistant_text=answer,
                        language=device.settings.language if device.settings else "ru",
                        stt_sec=timing.get("stt", 0), llm_sec=timing.get("llm", 0),
                        tts_sec=timing.get("tts", 0)))
    db.commit()


def config_payload(db: Session, settings: DeviceSettings) -> dict:
    """Сообщение `config` для прошивки: настройки + (если есть) модель слова пробуждения {sha256, size}."""
    payload = DeviceSettingsOut.model_validate(settings).model_dump()
    payload["wake_word"] = safe_wake_word(settings.wake_word)    # слова, которых сервер больше не предлагает, → «Нильс»
    model = db.scalar(select(WakeModel).where(WakeModel.wake_word == payload["wake_word"]))
    payload["wake_model"] = {"sha256": model.sha256, "size": model.size_bytes} if model else None
    return payload


def seed_wake_models(db: Session, models_dir=None) -> list[str]:
    """Импортирует готовые модели слов из wakeword/models/*.nwwm (входят в проект), если их ещё нет на сервере.
    Модель, загруженная администратором через API, не перезаписывается. Возвращает id импортированных слов."""
    import hashlib
    import shutil
    from pathlib import Path

    from . import nwwm
    from .config import BASE_DIR  # читается при вызове: тесты подменяют

    src = Path(models_dir or settings.wake_models_dir)
    dst = BASE_DIR / "data" / "wakewords"
    added = []
    for path in sorted(src.glob("*.nwwm")) if src.exists() else []:
        blob = path.read_bytes()
        try:
            manifest, _ = nwwm.parse(blob)
        except nwwm.ContainerError as e:
            log.warning("Модель %s пропущена: %s", path.name, e)
            continue
        word = manifest["wake_word"]
        if db.scalar(select(WakeModel).where(WakeModel.wake_word == word)):
            continue
        sha = hashlib.sha256(blob).hexdigest()
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dst / f"{sha}.nwwm")
        db.add(WakeModel(wake_word=word, label=str(manifest.get("label", word))[:80], sha256=sha,
                         size_bytes=len(blob), filename=f"{sha}.nwwm", notes="seed"))
        added.append(word)
    db.commit()
    return added
