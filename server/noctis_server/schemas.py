"""Pydantic-схемы запросов и ответов API."""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .catalog import CHARACTERS, LANGUAGES, is_valid_wake_word
from .password_policy import policy_error

Language = Literal[LANGUAGES]  # type: ignore[valid-type]
Theme = Literal["light", "dark", "system"]
VoiceGender = Literal["male", "female"]
Character = Literal[CHARACTERS]  # type: ignore[valid-type]


def _check_timezone(value):
    """Часовой пояс — только настоящее имя IANA (Europe/Moscow); иначе будильники считались бы по умолчанию молча."""
    if value is None:
        return value
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(value)
    except Exception:
        raise ValueError("unknown time zone (use IANA name, e.g. Europe/Moscow)")
    return value


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Auth ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=80)
    consent: bool = Field(description="Согласие на обработку персональных данных (152-ФЗ)")
    language: Language = "ru"

    @model_validator(mode="after")
    def _strong_password(self):
        reason = policy_error(self.password, str(self.email), self.display_name)
        if reason:
            raise ValueError(reason)
        return self


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strong(cls, v: str) -> str:
        reason = policy_error(v)
        if reason:
            raise ValueError(reason)
        return v


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strong(cls, v: str) -> str:
        reason = policy_error(v)
        if reason:
            raise ValueError(reason)
        return v


# ---------- User ----------
class UserSettingsOut(ORM):
    wake_word: str
    voice_gender: VoiceGender
    character_id: str
    language: Language
    theme: Theme
    history_enabled: bool
    data_collection: bool


def _check_wake_word(v: str | None) -> str | None:
    if v is not None and not is_valid_wake_word(v):
        raise ValueError("wake_word must be 'nils' or 'alexa'")
    return v


_CITY_RE = re.compile(r"^[^\W\d_][\w .,'’()\-]{0,78}$", re.UNICODE)


def _check_home_city(v: str | None) -> str | None:
    """Название города/места: буквы, пробелы, дефис, точка, запятая, апостроф, скобки (до 80 символов); пустая строка очищает."""
    if v is None:
        return None
    v = " ".join(v.split())
    if v and not _CITY_RE.match(v):
        raise ValueError("home_city must be a place name (letters, spaces, hyphens), up to 80 characters")
    return v


class UserSettingsPatch(BaseModel):
    wake_word: str | None = Field(default=None, min_length=1, max_length=64)
    voice_gender: VoiceGender | None = None
    character_id: Character | None = None
    language: Language | None = None
    theme: Theme | None = None
    history_enabled: bool | None = None
    data_collection: bool | None = None

    _wake = field_validator("wake_word")(_check_wake_word)


class UserOut(ORM):
    id: int
    email: EmailStr
    display_name: str
    role: str
    created_at: datetime
    settings: UserSettingsOut


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)


class DeleteAccountIn(BaseModel):
    password: str


# ---------- Devices ----------
class DeviceSettingsOut(ORM):
    wake_word: str
    voice_gender: VoiceGender
    character_id: str
    language: Language
    volume: int
    timezone: str
    home_city: str = ""
    revision: int


class DeviceSettingsPatch(BaseModel):
    wake_word: str | None = Field(default=None, min_length=1, max_length=64)
    voice_gender: VoiceGender | None = None
    character_id: Character | None = None
    language: Language | None = None
    volume: int | None = Field(default=None, ge=0, le=100)
    timezone: str | None = Field(default=None, max_length=64)
    home_city: str | None = Field(default=None, max_length=80)

    _wake = field_validator("wake_word")(_check_wake_word)
    _tz = field_validator("timezone")(_check_timezone)
    _city = field_validator("home_city")(_check_home_city)


class DeviceCreate(BaseModel):
    name: str = Field(default="Noctis", max_length=80)
    timezone: str = "Europe/Moscow"

    _tz = field_validator("timezone")(_check_timezone)


class DeviceOut(ORM):
    public_id: str
    name: str
    firmware_version: str
    last_seen: datetime | None
    online: bool = False
    enabled: bool
    settings: DeviceSettingsOut


class DeviceCreated(DeviceOut):
    device_token: str = Field(description="Показывается один раз; записывается в прошивку при сопряжении")


class DevicePatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None


class WifiChangeIn(BaseModel):
    """Смена сети Wi-Fi у включённой колонки. Пароль только пересылается колонке: не хранится и не пишется в журнал."""
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def _check(self):
        if len(self.ssid.encode("utf-8")) > 32 or any(ord(c) < 32 or ord(c) == 127 for c in self.ssid):
            raise ValueError("ssid: 1-32 bytes, no control characters")
        n = len(self.password.encode("utf-8"))
        if self.password and not (8 <= n <= 64):
            raise ValueError("password: empty (open network) or 8-64 characters")
        return self


class WifiStatusOut(BaseModel):
    state: str                      # none | pending | ok | failed | timeout
    ssid: str = ""
    error: str = ""
    request_id: str = ""


# ---------- History ----------
class HistoryOut(ORM):
    id: int
    created_at: datetime
    user_text: str
    assistant_text: str
    language: str


# ---------- OTA ----------
class OtaOut(ORM):
    version: str
    sha256: str
    size_bytes: int
    notes: str
    is_latest: bool
    created_at: datetime


class OtaTrigger(BaseModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
