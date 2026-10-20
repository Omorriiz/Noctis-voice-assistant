"""ORM-модели. Полная схема БД проекта (таймеры, будильники и медиа работают с этапа 5)."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)  # user | admin


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80), default="")
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # согласие на обработку ПДн (152-ФЗ)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[Role] = relationship()
    settings: Mapped["UserSettings"] = relationship(back_populates="user", uselist=False,
                                                    cascade="all, delete-orphan")
    sessions: Mapped[list["AuthSession"]] = relationship(cascade="all, delete-orphan")
    devices: Mapped[list["UserDevice"]] = relationship(cascade="all, delete-orphan")


class AuditLog(TimestampMixin, Base):
    """Журнал действий администратора. Хранит e-mail администратора отдельно, чтобы запись пережила его удаление."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    admin_email: Mapped[str] = mapped_column(String(254), default="")
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(String(254), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")


class UserSettings(Base):
    """Персонализация и предпочтения пользователя (общие для его устройств по умолчанию)."""
    __tablename__ = "user_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    wake_word: Mapped[str] = mapped_column(String(64), default="nils")
    voice_gender: Mapped[str] = mapped_column(String(8), default="male")  # male | female
    character_id: Mapped[str] = mapped_column(String(32), default="friendly")
    language: Mapped[str] = mapped_column(String(5), default="ru")        # ru en es de fr
    theme: Mapped[str] = mapped_column(String(8), default="system")       # light dark system
    history_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    data_collection: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="settings")


class AuthSession(TimestampMixin, Base):
    """Сессия входа: хранится хэш refresh-токена (ротация при каждом обновлении)."""
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    user_agent: Mapped[str] = mapped_column(String(200), default="")


class PasswordReset(TimestampMixin, Base):
    __tablename__ = "password_resets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Device(TimestampMixin, Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # виден устройству и приложению
    token_hash: Mapped[str] = mapped_column(String(64))  # хэш секрета устройства
    name: Mapped[str] = mapped_column(String(80), default="Noctis")
    firmware_version: Mapped[str] = mapped_column(String(32), default="")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    settings: Mapped["DeviceSettings"] = relationship(back_populates="device", uselist=False,
                                                      cascade="all, delete-orphan")
    owners: Mapped[list["UserDevice"]] = relationship(cascade="all, delete-orphan")


class DeviceSettings(Base):
    """Таблица `settings`: настройки конкретного устройства (синхронизируются с прошивкой)."""
    __tablename__ = "settings"
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    wake_word: Mapped[str] = mapped_column(String(64), default="nils")
    voice_gender: Mapped[str] = mapped_column(String(8), default="male")
    character_id: Mapped[str] = mapped_column(String(32), default="friendly")
    language: Mapped[str] = mapped_column(String(5), default="ru")
    volume: Mapped[int] = mapped_column(Integer, default=70)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    home_city: Mapped[str] = mapped_column(String(80), default="", server_default="")   # для «какая погода?» без названия места
    revision: Mapped[int] = mapped_column(Integer, default=1)  # растёт при каждом изменении → прошивка видит обновление

    device: Mapped[Device] = relationship(back_populates="settings")


class UserDevice(Base):
    __tablename__ = "user_devices"
    __table_args__ = (UniqueConstraint("user_id", "device_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=True)

    device: Mapped[Device] = relationship(overlaps="owners")


class Timer(TimestampMixin, Base):
    __tablename__ = "timers"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80), default="")
    duration_sec: Mapped[int] = mapped_column(Integer)
    remaining_sec: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(10), default="running")  # running paused finished cancelled
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # только у running
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Alarm(TimestampMixin, Base):
    __tablename__ = "alarms"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80), default="")
    hour: Mapped[int] = mapped_column(Integer)
    minute: Mapped[int] = mapped_column(Integer)
    weekdays: Mapped[list] = mapped_column(JSON, default=list)  # [] = однократно, иначе 0..6 (пн..вс)
    one_shot_date: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD для однократного
    melody: Mapped[str] = mapped_column(String(40), default="default")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fired_key: Mapped[str] = mapped_column(String(16), default="")  # "YYYY-MM-DD HH:MM" — защита от повторного звонка


class MediaTrack(TimestampMixin, Base):
    __tablename__ = "media"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    artist: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(16))  # local | jamendo | radio
    uri: Mapped[str] = mapped_column(Text)           # путь к файлу или URL потока
    duration_sec: Mapped[float] = mapped_column(Float, default=0)
    license: Mapped[str] = mapped_column(String(120), default="")
    album: Mapped[str] = mapped_column(String(200), default="")
    genre: Mapped[str] = mapped_column(String(80), default="")
    external_id: Mapped[str] = mapped_column(String(64), default="")  # id трека в Jamendo


class Playlist(TimestampMixin, Base):
    __tablename__ = "playlists"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    items: Mapped[list["PlaylistItem"]] = relationship(cascade="all, delete-orphan",
                                                       order_by="PlaylistItem.position")


class PlaylistItem(Base):
    __tablename__ = "playlist_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)


class HistoryEntry(TimestampMixin, Base):
    """История диалога. Привязана к устройству; хранится, только если включена в настройках."""
    __tablename__ = "history"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    user_text: Mapped[str] = mapped_column(Text)
    assistant_text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(5), default="ru")
    stt_sec: Mapped[float] = mapped_column(Float, default=0)
    llm_sec: Mapped[float] = mapped_column(Float, default=0)
    tts_sec: Mapped[float] = mapped_column(Float, default=0)


class MqttTopic(Base):
    __tablename__ = "mqtt_topics"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    direction: Mapped[str] = mapped_column(String(8), default="sub")  # sub | pub
    description: Mapped[str] = mapped_column(String(200), default="")


class OtaVersion(TimestampMixin, Base):
    __tablename__ = "ota_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    filename: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    signature: Mapped[str] = mapped_column(Text, default="")  # подпись образа (этап 8)
    notes: Mapped[str] = mapped_column(Text, default="")
    is_latest: Mapped[bool] = mapped_column(Boolean, default=False)


class WakeModel(TimestampMixin, Base):
    """Модель слова пробуждения (контейнер NWWM) для слова `nils` (у «Alexa» готовая модель Espressif в прошивке)."""
    __tablename__ = "wake_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    wake_word: Mapped[str] = mapped_column(String(80), unique=True)
    label: Mapped[str] = mapped_column(String(80), default="")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(80))
    notes: Mapped[str] = mapped_column(Text, default="")
