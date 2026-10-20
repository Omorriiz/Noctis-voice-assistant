"""Зависимости FastAPI: текущий пользователь, проверка прав, доступ к устройству."""
import jwt
import hmac

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Device, User, UserDevice
from .security import decode_access_token, sha256

bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    if creds is None:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(401, "User not found")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role.name != "admin":
        raise HTTPException(403, "Admin only")
    return user


def owned_device(public_id: str, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> Device:
    """Устройство, привязанное к текущему пользователю (иначе 404, чтобы не раскрывать чужие)."""
    device = db.scalar(select(Device).join(UserDevice, UserDevice.device_id == Device.id)
                       .where(Device.public_id == public_id, UserDevice.user_id == user.id))
    if device is None:
        raise HTTPException(404, "Device not found")
    return device


def current_device(x_device_id: str | None = Header(default=None), x_device_token: str | None = Header(default=None),
                   db: Session = Depends(get_db)) -> Device:
    """Аутентификация самого устройства (прошивка): заголовки X-Device-Id и X-Device-Token."""
    device = db.scalar(select(Device).where(Device.public_id == (x_device_id or ""))) if x_device_id else None
    if (device is None or not device.enabled or not x_device_token
            or not hmac.compare_digest(device.token_hash, sha256(x_device_token))):
        raise HTTPException(401, "Invalid device credentials")
    return device
