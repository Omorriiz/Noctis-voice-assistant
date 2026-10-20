"""Собственная музыкальная библиотека пользователя: <music_dir>/<id пользователя>/*.mp3|flac|ogg|wav.
Метаданные читает mutagen (теги ID3/Vorbis/FLAC); если тегов нет — название берётся из имени файла."""
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import MediaTrack

log = logging.getLogger("noctis.library")

EXTENSIONS = {".mp3", ".flac", ".ogg", ".wav"}
NAME_RE = re.compile(r"[^\w .,()'&+\-]", re.UNICODE)


class LibraryError(ValueError):
    pass


def user_dir(user_id: int) -> Path:
    return Path(settings.music_dir) / str(user_id)


def safe_filename(name: str) -> str:
    """Только имя файла (без путей), допустимые символы и расширение из белого списка."""
    base = Path(name.replace("\\", "/")).name
    base = NAME_RE.sub("_", base).strip(" .")
    if not base or Path(base).suffix.lower() not in EXTENSIONS:
        raise LibraryError("unsupported file type")
    return base[:150]


def resolve(user_id: int, relative: str) -> Path:
    """Путь внутри папки пользователя; выход за её пределы (../) запрещён."""
    root = user_dir(user_id).resolve()
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise LibraryError("path outside library")
    return path


def read_tags(path: Path) -> dict:
    title, artist, album, genre, duration = path.stem, "", "", "", 0.0
    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
        if audio is not None:
            def first(*keys):               # MP3/FLAC/OGG — «title», у WAV с ID3-тегами — «TIT2»
                for key in keys:
                    v = audio.get(key)
                    if v:
                        return str(v[0] if isinstance(v, list) else v).strip()
                return ""
            title = first("title", "TIT2") or title
            artist, album, genre = first("artist", "TPE1"), first("album", "TALB"), first("genre", "TCON")
            duration = float(getattr(audio.info, "length", 0) or 0)
    except Exception as e:                       # битый файл/теги не должны ломать сканирование
        log.warning("теги %s не прочитаны: %s", path.name, e)
    return {"title": title[:200], "artist": artist[:200], "album": album[:200], "genre": genre[:80],
            "duration_sec": duration}


def _upsert(db: Session, user_id: int, path: Path) -> MediaTrack:
    rel = path.relative_to(user_dir(user_id).resolve()).as_posix()
    track = db.scalar(select(MediaTrack).where(MediaTrack.owner_id == user_id, MediaTrack.source == "local",
                                               MediaTrack.uri == rel))
    tags = read_tags(path)
    if track is None:
        track = MediaTrack(owner_id=user_id, source="local", uri=rel, license="own")
        db.add(track)
    for key, value in tags.items():
        setattr(track, key, value)
    return track


def scan(db: Session, user_id: int) -> dict:
    """Синхронизирует БД с папкой: добавляет новые файлы, обновляет теги, удаляет записи пропавших файлов."""
    root = user_dir(user_id)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    found = {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS}
    existing = {t.uri: t for t in db.scalars(select(MediaTrack).where(MediaTrack.owner_id == user_id,
                                                                      MediaTrack.source == "local")).all()}
    added = sum(1 for rel in found if rel not in existing)
    for path in found.values():
        _upsert(db, user_id, path)
    removed = 0
    for rel, track in existing.items():
        if rel not in found:
            db.delete(track)
            removed += 1
    db.commit()
    return {"added": added, "removed": removed, "total": len(found)}


def save_upload(db: Session, user_id: int, filename: str, data: bytes) -> MediaTrack:
    if len(data) > settings.music_upload_max_mb * 1024 * 1024:
        raise LibraryError("file too large")
    if not data:
        raise LibraryError("empty file")
    name = safe_filename(filename)
    root = user_dir(user_id)
    root.mkdir(parents=True, exist_ok=True)
    path = resolve(user_id, name)
    path.write_bytes(data)
    track = _upsert(db, user_id, path)
    db.commit()
    return track


def delete_track(db: Session, track: MediaTrack) -> None:
    if track.source == "local" and track.owner_id is not None:
        try:
            resolve(track.owner_id, track.uri).unlink(missing_ok=True)
        except LibraryError:
            pass
    db.delete(track)
    db.commit()


def audio_uri(track: MediaTrack) -> str:
    """Что отдать декодеру: абсолютный путь (local) или URL потока."""
    if track.source == "local":
        return str(resolve(track.owner_id, track.uri))
    return track.uri


# ------------------------------------------------------------------ поиск
def _haystack(t: MediaTrack) -> str:
    return f"{t.title} {t.artist} {t.album} {t.genre}".casefold().replace("ё", "е")


# жанры в речи ↔ жанры в тегах (теги обычно английские): «включи джаз» находит Jazz
GENRE_ALIASES = {
    "джаз": "jazz", "рок": "rock", "поп": "pop", "классика": "classical", "классическая": "classical",
    "классику": "classical", "электроника": "electronic", "электронная": "electronic", "блюз": "blues",
    "рэп": "rap", "метал": "metal", "регги": "reggae", "фолк": "folk", "кантри": "country", "релакс": "ambient",
    "clasica": "classical", "klassik": "classical", "classique": "classical", "klassische": "classical",
}


def _variants(word: str) -> set[str]:
    out = {word}
    for key, alias in GENRE_ALIASES.items():
        if word == key or (len(key) >= 4 and word.startswith(key[:-1])):
            out.add(alias)
    return out


def search(db: Session, user_id: int, query: str, source: str = "local", limit: int = 50) -> list[MediaTrack]:
    """Поиск по названию/исполнителю/альбому/жанру: все слова запроса должны встретиться (регистр не важен)."""
    words = [w for w in query.casefold().replace("ё", "е").split() if w]
    tracks = db.scalars(select(MediaTrack).where(MediaTrack.owner_id == user_id, MediaTrack.source == source)
                        .order_by(MediaTrack.artist, MediaTrack.album, MediaTrack.title)).all()
    hits = [t for t in tracks if all(any(v in _haystack(t) for v in _variants(w)) or _stem_match(w, _haystack(t))
                                     for w in words)]
    return hits[:limit]


def _stem_match(word: str, haystack: str) -> bool:
    """«рока» находит «рок», «заря» — «зари»: сравнение по основе слова (≥ 5 букв)."""
    return len(word) >= 5 and word[:-1] in haystack
