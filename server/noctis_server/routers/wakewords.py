"""Модели слова пробуждения: загрузка (админ), список и выдача устройствам."""
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import nwwm, registry
from ..config import BASE_DIR
from ..db import get_db
from ..deps import current_device, current_user, require_admin
from ..models import Device, DeviceSettings, User, WakeModel
from ..services import config_payload

router = APIRouter(prefix="/api/v1", tags=["wakewords"])
WAKE_DIR = BASE_DIR / "data" / "wakewords"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class WakeModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    wake_word: str
    label: str
    sha256: str
    size_bytes: int


@router.get("/wakewords", response_model=list[WakeModelOut])
def list_models(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(WakeModel).order_by(WakeModel.wake_word)).all()


@router.put("/wakewords", response_model=WakeModelOut, status_code=201)
async def upload_model(request: Request, wake_word: str = Query(..., max_length=80), notes: str = "",
                       _: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Тело — контейнер NWWM (его собирает wakeword/pack_model.py). Заменяет прежнюю модель этого слова;
    включённые устройства с этим словом сразу получают новый `config`."""
    if wake_word != "nils":
        raise HTTPException(422, "Models exist only for 'nils' (Alexa uses the built-in Espressif model)")
    blob = await request.body()
    try:
        manifest, _model = nwwm.parse(blob)
    except nwwm.ContainerError as e:
        raise HTTPException(422, f"Invalid model container: {e}")
    sha = hashlib.sha256(blob).hexdigest()
    WAKE_DIR.mkdir(parents=True, exist_ok=True)
    (WAKE_DIR / f"{sha}.nwwm").write_bytes(blob)
    row = db.scalar(select(WakeModel).where(WakeModel.wake_word == wake_word))
    if row is None:
        row = WakeModel(wake_word=wake_word)
        db.add(row)
    row.label, row.sha256, row.size_bytes = str(manifest.get("label", wake_word))[:80], sha, len(blob)
    row.filename, row.notes = f"{sha}.nwwm", notes
    db.commit()

    for ds in db.scalars(select(DeviceSettings).where(DeviceSettings.wake_word == wake_word)):
        conn = registry.online_devices.get(ds.device.public_id)
        if conn is not None:
            await conn.push_config(config_payload(db, ds))
    return row


@router.delete("/wakewords", status_code=204)
def delete_model(wake_word: str = Query(...), _: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.scalar(select(WakeModel).where(WakeModel.wake_word == wake_word))
    if row is None:
        raise HTTPException(404, "No model for this wake word")
    db.delete(row)
    db.commit()


@router.get("/device/wakeword/{sha256}")
def download_model(sha256: str, _: Device = Depends(current_device), db: Session = Depends(get_db)):
    """Контейнер модели для устройства (по хэшу содержимого); прошивка проверяет хэш перед записью во flash."""
    row = db.scalar(select(WakeModel).where(WakeModel.sha256 == sha256)) if SHA_RE.match(sha256) else None
    path = (WAKE_DIR / row.filename) if row else None
    if row is None or not path.exists():
        raise HTTPException(404, "Model not found")
    return FileResponse(path, media_type="application/octet-stream", filename=row.filename)
