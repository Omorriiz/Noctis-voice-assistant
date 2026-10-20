"""Профиль пользователя и персональные настройки."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import privacy

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..schemas import DeleteAccountIn, UserOut, UserPatch, UserSettingsOut, UserSettingsPatch
from ..security import verify_password

router = APIRouter(prefix="/api/v1/me", tags=["profile"])


def _out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, display_name=user.display_name, role=user.role.name,
                   created_at=user.created_at, settings=UserSettingsOut.model_validate(user.settings))


@router.get("", response_model=UserOut)
def get_me(user: User = Depends(current_user)):
    return _out(user)


@router.patch("", response_model=UserOut)
def patch_me(body: UserPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.display_name is not None:
        user.display_name = body.display_name
    db.commit()
    return _out(user)


@router.get("/settings", response_model=UserSettingsOut)
def get_settings(user: User = Depends(current_user)):
    return user.settings


@router.patch("/settings", response_model=UserSettingsOut)
def patch_settings(body: UserSettingsPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(user.settings, key, value)
    db.commit()
    return user.settings


@router.get("/export")
def export_my_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Все данные пользователя файлом JSON (право на доступ к ПДн, 152-ФЗ). Только по запросу пользователя."""
    body = json.dumps(privacy.export_user(db, user), ensure_ascii=False, indent=2)
    name = f"noctis-data-{datetime.now(timezone.utc):%Y%m%d}.json"
    return Response(body, media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.delete("/history")
def clear_my_history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Очистить историю диалогов всех своих колонок."""
    return {"deleted": privacy.clear_history(db, user)}


@router.delete("", status_code=204)
async def delete_account(body: DeleteAccountIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Удаление аккаунта и всех связанных данных: колонки с историей, таймерами и будильниками, медиатека и загруженные файлы
    (право на удаление ПДн, 152-ФЗ). Включённые колонки сбрасываются и возвращаются в режим сопряжения."""
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(403, "Password is incorrect")
    await privacy.erase_user(db, user)
