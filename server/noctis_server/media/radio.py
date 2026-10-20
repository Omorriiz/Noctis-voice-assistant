"""Интернет-радио: станции пользователя (название + URL потока MP3/Ogg/FLAC). Список ведёт сам пользователь
(приложение/API) или файл data/radio_stations.json; встроенных адресов нет — потоки радиостанций меняются."""
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import MediaTrack

log = logging.getLogger("noctis.radio")


class RadioError(ValueError):
    pass


def validate_url(url: str) -> str:
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        raise RadioError("stream url must be http(s)")
    return url.strip()


def add_station(db: Session, user_id: int, name: str, url: str) -> MediaTrack:
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise RadioError("name")
    url = validate_url(url)
    station = db.scalar(select(MediaTrack).where(MediaTrack.owner_id == user_id, MediaTrack.source == "radio",
                                                 MediaTrack.uri == url))
    if station is None:
        station = MediaTrack(owner_id=user_id, source="radio", uri=url, license="stream")
        db.add(station)
    station.title = name
    db.commit()
    return station


def list_stations(db: Session, user_id: int) -> list[MediaTrack]:
    return list(db.scalars(select(MediaTrack).where(MediaTrack.owner_id == user_id, MediaTrack.source == "radio")
                           .order_by(MediaTrack.title)))


def find_station(db: Session, user_id: int, query: str) -> list[MediaTrack]:
    """Станции, в названии которых есть все слова запроса; пустой запрос — все станции."""
    words = [w for w in query.casefold().replace("ё", "е").split() if w]
    stations = list_stations(db, user_id)
    return [s for s in stations if all(w in s.title.casefold().replace("ё", "е") for w in words)]


def import_file(db: Session, user_id: int, path: str | None = None) -> int:
    """Загружает станции из JSON-файла [{"name","url"}]; неверные записи пропускаются. Возвращает число добавленных."""
    p = Path(path or settings.radio_stations_file)
    if not p.exists():
        return 0
    added = 0
    try:
        for item in json.loads(p.read_text(encoding="utf-8")):
            try:
                add_station(db, user_id, str(item["name"]), str(item["url"]))
                added += 1
            except (KeyError, RadioError, TypeError) as e:
                log.warning("станция пропущена: %s", e)
    except (ValueError, OSError) as e:
        log.warning("файл станций %s не прочитан: %s", p, e)
    return added
