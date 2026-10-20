"""Устройства пользователя: привязка, настройки, управление, история диалогов."""
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import registry
from ..db import get_db
from ..deps import current_user, owned_device
from ..models import Device, HistoryEntry, OtaVersion, User, UserDevice
from ..schemas import (OtaTrigger, DeviceCreate, DeviceCreated, DeviceOut, DevicePatch, DeviceSettingsOut,
                       DeviceSettingsPatch, HistoryOut, WifiChangeIn, WifiStatusOut)
from ..services import config_payload, create_device, device_out

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


@router.get("", response_model=list[DeviceOut])
def list_devices(user: User = Depends(current_user), db: Session = Depends(get_db)):
    devices = db.scalars(select(Device).join(UserDevice, UserDevice.device_id == Device.id)
                         .where(UserDevice.user_id == user.id).order_by(Device.id)).all()
    return [device_out(d) for d in devices]


@router.post("", response_model=DeviceCreated, status_code=201)
def add_device(body: DeviceCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Создаёт устройство и выдаёт его секрет (один раз). Приложение передаёт его прошивке
    при BLE-сопряжении."""
    device, secret = create_device(db, user, body.name, body.timezone)
    return DeviceCreated(**device_out(device).model_dump(), device_token=secret)


@router.get("/{public_id}", response_model=DeviceOut)
def get_device(device: Device = Depends(owned_device)):
    return device_out(device)


@router.patch("/{public_id}", response_model=DeviceOut)
def patch_device(body: DevicePatch, device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(device, key, value)
    db.commit()
    return device_out(device)


@router.delete("/{public_id}", status_code=204)
async def remove_device(device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    """Удалить колонку. Если она на связи, ей уходит сброс к заводским настройкам: она вернётся в режим сопряжения
    (иначе плата осталась бы с токеном удалённого устройства и получала бы отказ)."""
    conn = registry.online_devices.get(device.public_id)
    if conn is not None:
        await conn.send_json({"type": "command", "name": "factory_reset"})
    db.delete(device)
    db.commit()


@router.get("/{public_id}/settings", response_model=DeviceSettingsOut)
def get_device_settings(device: Device = Depends(owned_device)):
    return device.settings


@router.patch("/{public_id}/settings", response_model=DeviceSettingsOut)
async def patch_device_settings(body: DeviceSettingsPatch, device: Device = Depends(owned_device),
                                db: Session = Depends(get_db)):
    changes = body.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(device.settings, key, value)
    if changes:
        device.settings.revision += 1
    db.commit()
    conn = registry.online_devices.get(device.public_id)
    if conn is not None:  # синхронизация с включённым устройством без перезагрузки
        await conn.push_config(config_payload(db, device.settings))
    return device.settings


@router.post("/{public_id}/reboot", status_code=202)
async def reboot(device: Device = Depends(owned_device)):
    conn = registry.online_devices.get(device.public_id)
    if conn is None:
        raise HTTPException(409, "Device is offline")
    await conn.send_json({"type": "command", "name": "reboot"})
    return {"detail": "sent"}


WIFI_CHANGE_TIMEOUT_SEC = 90   # плата ждёт новую сеть до 45 с, затем возвращается в прежнюю и сообщает об этом


def _wifi_status(public_id: str) -> WifiStatusOut:
    ch = registry.wifi_changes.get(public_id)
    if ch is None:
        return WifiStatusOut(state="none")
    state = ch["state"]
    if state == "pending" and time.monotonic() - ch["at"] > WIFI_CHANGE_TIMEOUT_SEC:
        state = "timeout"
    return WifiStatusOut(state=state, ssid=ch["ssid"], error=ch.get("error", ""), request_id=ch["request_id"])


@router.post("/{public_id}/wifi", response_model=WifiStatusOut, status_code=202)
async def change_wifi(body: WifiChangeIn, device: Device = Depends(owned_device)):
    """Смена сети Wi-Fi без сброса. Только у колонки, которая сейчас на связи. Если новая сеть не сработала,
    колонка сама возвращается в прежнюю (итог — GET .../wifi). Для офлайн-колонки используйте Bluetooth в приложении."""
    conn = registry.online_devices.get(device.public_id)
    if conn is None:
        raise HTTPException(409, "Device is offline")
    if _wifi_status(device.public_id).state == "pending":
        raise HTTPException(409, "A Wi-Fi change is already in progress")
    request_id = secrets.token_hex(4)
    registry.wifi_changes[device.public_id] = {"request_id": request_id, "ssid": body.ssid, "state": "pending",
                                               "error": "", "at": time.monotonic()}
    await conn.send_json({"type": "command", "name": "set_wifi", "ssid": body.ssid, "password": body.password,
                          "request_id": request_id})
    return _wifi_status(device.public_id)


@router.get("/{public_id}/wifi", response_model=WifiStatusOut)
def wifi_status(device: Device = Depends(owned_device)):
    return _wifi_status(device.public_id)


@router.get("/{public_id}/history", response_model=list[HistoryOut])
def get_history(limit: int = 50, offset: int = 0, device: Device = Depends(owned_device),
                db: Session = Depends(get_db)):
    limit = max(1, min(limit, 200))
    return db.scalars(select(HistoryEntry).where(HistoryEntry.device_id == device.id)
                      .order_by(HistoryEntry.id.desc()).limit(limit).offset(max(0, offset))).all()


@router.get("/{public_id}/history/export")
def export_history(device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    rows = db.scalars(select(HistoryEntry).where(HistoryEntry.device_id == device.id)
                      .order_by(HistoryEntry.id)).all()
    data = [HistoryOut.model_validate(r).model_dump(mode="json") for r in rows]
    return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="history-{device.public_id}.json"'})


@router.delete("/{public_id}/history", status_code=204)
def clear_history(device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    db.execute(delete(HistoryEntry).where(HistoryEntry.device_id == device.id))
    db.commit()


@router.post("/{public_id}/ota", status_code=202)
async def trigger_ota(body: OtaTrigger, device: Device = Depends(owned_device), db: Session = Depends(get_db)):
    """Отправляет включённому устройству команду обновиться до указанной версии (из ota_versions)."""
    row = db.scalar(select(OtaVersion).where(OtaVersion.version == body.version))
    if row is None:
        raise HTTPException(404, "Firmware version not found")
    conn = registry.online_devices.get(device.public_id)
    if conn is None:
        raise HTTPException(409, "Device is offline")
    registry.ota_status.pop(device.public_id, None)
    await conn.send_json({"type": "command", "name": "ota", "version": row.version, "sha256": row.sha256,
                          "size": row.size_bytes, "url": f"/api/v1/device/ota/{row.version}"})
    return {"detail": "sent", "version": row.version}


@router.get("/{public_id}/ota")
def ota_state(device: Device = Depends(owned_device)):
    """Последний статус обновления, который сообщила прошивка."""
    return registry.ota_status.get(device.public_id, {"state": "none"})
