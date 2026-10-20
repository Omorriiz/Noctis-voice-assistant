"""Персонализация: справочник, образцы характеров, предпрослушивание голоса, применение ко всем устройствам."""
import asyncio
import io
import wave

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import personalization as pz
from .. import registry
from ..catalog import CHARACTER_INFO, CHARACTERS, LANGUAGES
from ..db import get_db
from ..deps import current_user
from ..models import Device, User, UserDevice, WakeModel
from ..services import config_payload

router = APIRouter(prefix="/api/v1/personalization", tags=["personalization"])


@router.get("/catalog")
def catalog(language: str = Query("ru", description="Язык названий и описаний: ru en es de fr"),
            db: Session = Depends(get_db)):
    """Все варианты персонализации с названиями на выбранном языке (для приложения и сайта)."""
    if language not in LANGUAGES:
        raise HTTPException(422, "Unsupported language")
    return pz.catalog_payload(language, {m.wake_word for m in db.scalars(select(WakeModel))})


@router.get("/characters/{character_id}/sample")
def character_sample(character_id: str, language: str = "ru"):
    """Пример реплики характера (текст) — для предпросмотра. Аудио: voice-preview."""
    if character_id not in CHARACTERS or language not in LANGUAGES:
        raise HTTPException(404, "Unknown character or language")
    info = CHARACTER_INFO[character_id]
    return {"id": character_id, "language": language, "name": info["name"][language],
            "description": info["description"][language], "sample": info["sample"][language]}


class VoicePreviewIn(BaseModel):
    language: str = "ru"
    voice_gender: str = "male"
    character_id: str | None = Field(default=None, description="Если задан и text пуст — озвучивается образец характера")
    text: str | None = Field(default=None, max_length=300)


def _wav(pcm: bytes, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


@router.post("/voice-preview", response_class=Response, responses={200: {"content": {"audio/wav": {}}}})
async def voice_preview(body: VoicePreviewIn, request: Request, _: User = Depends(current_user)):
    """Образец голоса (WAV, 16 кГц) на выбранном языке; нужен загруженный голосовой конвейер."""
    if body.language not in LANGUAGES or body.voice_gender not in ("male", "female"):
        raise HTTPException(422, "Unsupported language or voice")
    if body.character_id is not None and body.character_id not in CHARACTERS:
        raise HTTPException(422, "Unknown character")
    gateway = request.app.state.gateway
    if gateway.assistant is None:
        raise HTTPException(503, "Voice pipeline is not loaded")
    text = body.text or CHARACTER_INFO[body.character_id or "friendly"]["sample"][body.language]
    settings = {"language": body.language, "voice_gender": body.voice_gender}
    async with gateway.pipeline_lock:  # GPU/модели не рассчитаны на параллельные запросы
        try:
            pcm = await asyncio.to_thread(gateway.assistant.tts_pcm16, text, settings)
        except Exception as e:
            raise HTTPException(502, f"TTS failed: {e}")
    return Response(_wav(pcm), media_type="audio/wav")


@router.post("/apply-to-devices")
async def apply_to_devices(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Копирует персонализацию профиля (слово, голос, характер, язык) на все устройства пользователя;
    включённые устройства получают её сразу."""
    us = user.settings
    devices = db.scalars(select(Device).join(UserDevice, UserDevice.device_id == Device.id)
                         .where(UserDevice.user_id == user.id)).all()
    updated = []
    for d in devices:
        d.settings.wake_word, d.settings.voice_gender = us.wake_word, us.voice_gender
        d.settings.character_id, d.settings.language = us.character_id, us.language
        d.settings.revision += 1
        updated.append(d)
    db.commit()
    for d in updated:
        conn = registry.online_devices.get(d.public_id)
        if conn is not None:
            await conn.push_config(config_payload(db, d.settings))
    return {"updated": [d.public_id for d in updated]}
