"""REST-API музыки: библиотека пользователя, радиостанции, Jamendo, плейлисты и плеер устройства.
Источников ровно три — local, jamendo, radio."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import registry, skills
from ..db import get_db
from ..deps import current_user, owned_device
from ..media import jamendo, library, radio
from ..media.player import Track
from ..models import Device, MediaTrack, Playlist, PlaylistItem, User

router = APIRouter(prefix="/api/v1", tags=["media"])
SOURCES = ("local", "jamendo", "radio")


class TrackOut(BaseModel):
    id: int
    title: str
    artist: str
    album: str
    genre: str
    source: str
    duration_sec: float
    license: str


class RadioIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=500)


class PlaylistIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class PlaylistItemsIn(BaseModel):
    track_ids: list[int] = Field(max_length=200)


class PlayIn(BaseModel):
    track_id: int | None = None
    playlist_id: int | None = None
    query: str = Field(default="", max_length=100)
    source: str | None = None


def _track_out(t: MediaTrack) -> TrackOut:
    return TrackOut(id=t.id, title=t.title, artist=t.artist, album=t.album, genre=t.genre, source=t.source,
                    duration_sec=t.duration_sec, license=t.license)


def _own_track(db: Session, user: User, track_id: int) -> MediaTrack:
    t = db.get(MediaTrack, track_id)
    if t is None or t.owner_id != user.id:
        raise HTTPException(404, "Track not found")
    return t


# ------------------------------------------------------------------ библиотека
@router.get("/media/tracks", response_model=list[TrackOut])
def tracks(source: str | None = Query(default=None), q: str = "", user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    if source is not None and source not in SOURCES:
        raise HTTPException(422, "Unknown source")
    rows = [t for s in ([source] if source else SOURCES) for t in library.search(db, user.id, q, source=s, limit=200)]
    return [_track_out(t) for t in rows]


@router.post("/media/library/scan")
def scan_library(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return library.scan(db, user.id)


@router.put("/media/library/{filename}", response_model=TrackOut, status_code=201)
async def upload(filename: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Загрузка файла в библиотеку: тело запроса — сам файл (mp3/flac/ogg/wav)."""
    limit = library.settings.music_upload_max_mb * 1024 * 1024
    if int(request.headers.get("content-length", 0) or 0) > limit:
        raise HTTPException(413, "File too large")
    data = await request.body()
    try:
        track = await asyncio.to_thread(library.save_upload, db, user.id, filename, data)
    except library.LibraryError as e:
        raise HTTPException(422, str(e))
    return _track_out(track)


@router.delete("/media/tracks/{track_id}", status_code=204)
def delete_track(track_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    library.delete_track(db, _own_track(db, user, track_id))


# ------------------------------------------------------------------ радио
@router.get("/media/radio", response_model=list[TrackOut])
def stations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_track_out(t) for t in radio.list_stations(db, user.id)]


@router.post("/media/radio", response_model=TrackOut, status_code=201)
def add_station(body: RadioIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        return _track_out(radio.add_station(db, user.id, body.name, body.url))
    except radio.RadioError as e:
        raise HTTPException(422, str(e))


@router.post("/media/radio/import")
def import_stations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Добавляет станции из файла NOCTIS_RADIO_STATIONS_FILE ([{"name","url"}]) в список текущего пользователя."""
    return {"added": radio.import_file(db, user.id)}


@router.delete("/media/radio/{station_id}", status_code=204)
def delete_station(station_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    station = _own_track(db, user, station_id)
    if station.source != "radio":
        raise HTTPException(404, "Station not found")
    db.delete(station)
    db.commit()


# ------------------------------------------------------------------ Jamendo
@router.get("/media/jamendo/search", response_model=list[TrackOut])
async def jamendo_search(q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not jamendo.configured():
        raise HTTPException(503, "Jamendo is not configured (NOCTIS_JAMENDO_CLIENT_ID)")
    try:
        results = await asyncio.to_thread(jamendo.search, q, 20)
    except jamendo.JamendoError as e:
        raise HTTPException(502, f"Jamendo error: {e}")
    return [_track_out(t) for t in jamendo.to_tracks(db, user.id, results)]


# ------------------------------------------------------------------ плейлисты
def _playlist(db: Session, user: User, playlist_id: int) -> Playlist:
    p = db.get(Playlist, playlist_id)
    if p is None or p.owner_id != user.id:
        raise HTTPException(404, "Playlist not found")
    return p


def _playlist_out(p: Playlist) -> dict:
    return {"id": p.id, "name": p.name, "track_ids": [i.media_id for i in p.items]}


@router.get("/media/playlists")
def playlists(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_playlist_out(p) for p in db.scalars(select(Playlist).where(Playlist.owner_id == user.id).order_by(Playlist.id))]


@router.post("/media/playlists", status_code=201)
def add_playlist(body: PlaylistIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = Playlist(owner_id=user.id, name=body.name)
    db.add(p)
    db.commit()
    return _playlist_out(p)


@router.put("/media/playlists/{playlist_id}/items")
def set_items(playlist_id: int, body: PlaylistItemsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _playlist(db, user, playlist_id)
    for tid in body.track_ids:
        _own_track(db, user, tid)
    p.items.clear()
    p.items.extend(PlaylistItem(media_id=tid, position=i) for i, tid in enumerate(body.track_ids))
    db.commit()
    return _playlist_out(p)


@router.delete("/media/playlists/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_playlist(db, user, playlist_id))
    db.commit()


# ------------------------------------------------------------------ плеер устройства
@router.get("/devices/{public_id}/player")
def player_state(device: Device = Depends(owned_device)):
    session = registry.players.get(device.public_id)
    return session.info() if session else {"state": "stopped", "index": 0, "total": 0, "track": None}


@router.post("/devices/{public_id}/player/play")
async def player_play(body: PlayIn, device: Device = Depends(owned_device), user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    if device.public_id not in registry.online_devices:
        raise HTTPException(409, "Device is offline")
    if body.track_id is not None:
        rows = [_own_track(db, user, body.track_id)]
    elif body.playlist_id is not None:
        rows = [db.get(MediaTrack, i.media_id) for i in _playlist(db, user, body.playlist_id).items]
    else:
        src = body.source if body.source in SOURCES else None
        if src == "radio":
            rows = radio.find_station(db, user.id, body.query)[:1]
        elif src == "jamendo":
            if not jamendo.configured():
                raise HTTPException(503, "Jamendo is not configured")
            try:
                rows = jamendo.to_tracks(db, user.id, await asyncio.to_thread(jamendo.search, body.query, 10))
            except jamendo.JamendoError as e:
                raise HTTPException(502, f"Jamendo error: {e}")
        else:
            rows = library.search(db, user.id, body.query)
    tracks = skills._tracks([r for r in rows if r is not None])
    if not tracks:
        raise HTTPException(404, "Nothing to play")
    session = skills.get_session(device.public_id)
    if not await session.play(tracks):
        raise HTTPException(502, f"Could not start playback: {session.last_error}")
    return session.info()


@router.post("/devices/{public_id}/player/{action}")
async def player_action(action: str, device: Device = Depends(owned_device)):
    if action not in ("pause", "resume", "stop", "next", "prev"):
        raise HTTPException(404, "Unknown action")
    session = registry.players.get(device.public_id)
    if session is None or session.state == "stopped":
        raise HTTPException(409, "Nothing is playing")
    await getattr(session, action)()
    return session.info()
