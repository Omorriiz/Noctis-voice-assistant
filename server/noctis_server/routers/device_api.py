"""API для самой прошивки (аутентификация устройства заголовками X-Device-Id / X-Device-Token)."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_device
from ..models import Device, OtaVersion
from ..schemas import DeviceSettingsOut
from .ota import OTA_DIR

router = APIRouter(prefix="/api/v1/device", tags=["device-firmware"])


@router.get("/config", response_model=DeviceSettingsOut)
def config(device: Device = Depends(current_device)):
    """Резервный способ получить настройки по HTTP (основной — сообщение config по WebSocket)."""
    return device.settings


@router.get("/ota/{version}")
def download_firmware(version: str, device: Device = Depends(current_device), db: Session = Depends(get_db)):
    """Образ прошивки для устройства. SHA-256 прошивка проверяет до переключения раздела."""
    row = db.scalar(select(OtaVersion).where(OtaVersion.version == version))
    path = (OTA_DIR / row.filename) if row else None
    if row is None or not path.exists():
        raise HTTPException(404, "Version not found")
    return FileResponse(path, media_type="application/octet-stream", filename=row.filename)
