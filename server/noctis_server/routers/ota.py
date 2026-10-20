"""OTA-обновления прошивки: загрузка образа (admin) и выдача устройствам.
Подпись образа добавляется на этапе 8; sha256 проверяется прошивкой уже сейчас."""
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..db import get_db
from ..deps import current_user, require_admin
from ..models import OtaVersion, User
from ..schemas import OtaOut

router = APIRouter(prefix="/api/v1/ota", tags=["ota"])
OTA_DIR = BASE_DIR / "data" / "ota"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
MAX_IMAGE_BYTES = 8 * 1024 * 1024


@router.get("/latest", response_model=OtaOut)
def latest(_: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(OtaVersion).where(OtaVersion.is_latest))
    if row is None:
        raise HTTPException(404, "No firmware published")
    return row


@router.put("/{version}", response_model=OtaOut, status_code=201)
async def upload(version: str, request: Request, notes: str = "", _: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """Тело запроса — бинарный образ прошивки (application/octet-stream)."""
    if not VERSION_RE.match(version):
        raise HTTPException(422, "Version must look like 1.2.3")
    if db.scalar(select(OtaVersion).where(OtaVersion.version == version)):
        raise HTTPException(409, "Version already exists")
    data = await request.body()
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image is empty or too large")
    OTA_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"noctis-{version}.bin"
    (OTA_DIR / filename).write_bytes(data)
    db.execute(update(OtaVersion).values(is_latest=False))
    row = OtaVersion(version=version, filename=filename, sha256=hashlib.sha256(data).hexdigest(),
                     size_bytes=len(data), notes=notes, is_latest=True)
    db.add(row)
    db.commit()
    return row


@router.get("/{version}/download")
def download(version: str, _: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(OtaVersion).where(OtaVersion.version == version))
    path = (OTA_DIR / row.filename) if row else None
    if row is None or not path.exists():
        raise HTTPException(404, "Version not found")
    return FileResponse(path, media_type="application/octet-stream", filename=row.filename)
