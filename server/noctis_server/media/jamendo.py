"""Jamendo (музыка с лицензиями Creative Commons) — публичный API v3.0. Нужен собственный client_id
(NOCTIS_JAMENDO_CLIENT_ID, бесплатно на developer.jamendo.com); без него источник выключен.

Проверено на настоящем API (октябрь 2026). Что выяснилось:
  * ответ `/tracks/` приходит со статусом success, но в 30–60 % запросов с пустым списком (105 байт) — это сбой на стороне
    Jamendo, а не «ничего не найдено». Поэтому запросы повторяются пачкой параллельно, пока не придёт непустой ответ;
  * теги жанров у Jamendo английские (jazz, rock, relaxation…), поэтому русские слова «джаз», «спокойная» сопоставляются тегам;
  * удачные ответы запоминаются на полчаса (быстрее и переживают сбой сети/Jamendo).
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import MediaTrack

TIMEOUT = 8.0
PARALLEL = 3                 # запросов в пачке
ROUNDS = 2                   # пачек подряд, если все пришли пустыми
ROUND_PAUSE_SEC = 0.3
CACHE_TTL_SEC = 30 * 60
transport: httpx.BaseTransport | None = None      # подменяется в тестах

_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


class JamendoError(RuntimeError):
    pass


class JamendoKeyError(JamendoError):
    """Jamendo не принял client_id (код 5 в ответе)."""


def configured() -> bool:
    return bool(settings.jamendo_client_id)


# ------------------------------------------------------------------ жанры
# Теги Jamendo — английские. Короткие русские слова сопоставляются целиком (чтобы «поп» не совпало с «популярная»),
# длинные — по началу слова.
RU_EXACT = {"джаз": "jazz", "рок": "rock", "поп": "pop", "рэп": "hiphop", "хипхоп": "hiphop", "метал": "metal", "металл": "metal",
            "фолк": "folk", "блюз": "blues", "хаус": "house", "транс": "trance", "техно": "techno", "соул": "soul",
            "кантри": "country", "регги": "reggae", "панк": "punk", "диско": "disco", "фанк": "funk", "эмбиент": "ambient",
            "амбиент": "ambient", "чилл": "chillout", "лаунж": "lounge", "лоуфай": "lofi", "лофай": "lofi"}
RU_PREFIX = {"электрон": "electronic", "классическ": "classical", "классик": "classical", "инструментал": "instrumental",
             "спокойн": "relaxation", "расслабл": "relaxation", "релакс": "relaxation", "акустическ": "acoustic",
             "танцева": "dance", "саундтрек": "soundtrack", "симфони": "symphonic", "фортепиан": "piano", "пианин": "piano",
             "гитар": "guitar", "скрипк": "violin", "хип-хоп": "hiphop", "медитатив": "meditation", "бодр": "energetic"}
EN_TAGS = {"jazz", "rock", "pop", "electronic", "techno", "house", "trance", "classical", "instrumental", "ambient", "relaxation",
           "chillout", "lounge", "acoustic", "dance", "soundtrack", "hiphop", "metal", "folk", "blues", "soul", "country",
           "reggae", "punk", "disco", "funk", "piano", "guitar", "violin", "lofi", "meditation", "energetic", "symphonic"}
EN_ALIASES = {"hip-hop": "hiphop", "rap": "hiphop", "chill": "chillout", "relax": "relaxation", "calm": "relaxation",
              "electro": "electronic", "edm": "electronic", "classic": "classical", "orchestral": "symphonic"}


MOODS = {"relaxation", "chillout", "meditation", "energetic", "lofi"}
FILLER = {"музыка", "музыку", "музыки", "песня", "песню", "песни", "трек", "треки", "что-нибудь", "что-то", "нибудь", "какую-нибудь",
          "любую", "любое", "немного", "хорошую", "хорошая", "включи", "поставь", "сыграй", "жанр", "жанра", "стиле", "стиль", "в",
          "из", "с", "на", "и", "для", "а", "the", "some", "music", "song", "songs", "track", "tracks", "play", "genre", "any",
          "популярную", "популярная", "популярное", "новую", "новая", "случайную", "случайная"}


def _token_tag(token: str) -> str | None:
    if token in RU_EXACT:
        return RU_EXACT[token]
    if token in EN_TAGS:
        return token
    if token in EN_ALIASES:
        return EN_ALIASES[token]
    for stem, tag in RU_PREFIX.items():
        if token.startswith(stem):
            return tag
    return None


def parse_query(query: str) -> tuple[str | None, str]:
    """(тег жанра, текст для поиска). «спокойный джаз» → (jazz, ""); «Daft Punk» → (None, "Daft Punk") — «punk» здесь часть имени,
    а не жанр: жанром считается запрос, где все слова — жанры или «пустые» (музыка, включи…)."""
    tokens = re.findall(r"[\w-]+", (query or "").lower().replace("ё", "е"))
    tags, rest = [], []
    for token in tokens:
        if token in FILLER:
            continue
        tag = _token_tag(token.replace("хип-хоп", "хипхоп"))
        if tag:
            tags.append(tag)
        else:
            rest.append(token)
    if rest:
        return None, " ".join((query or "").split())      # есть слова вне жанров: ищем по тексту как есть
    if tags:
        return sorted(tags, key=lambda t: t in MOODS)[0], ""   # жанр важнее настроения: «спокойный джаз» → jazz
    return None, ""                                       # одни «пустые» слова: просто популярное


def genre_tag(query: str) -> str | None:
    return parse_query(query)[0]


def license_label(url: str) -> str:
    """http://creativecommons.org/licenses/by-nc-nd/3.0/ → «CC BY-NC-ND 3.0»."""
    m = re.search(r"creativecommons\.org/(licenses|publicdomain)/([a-z-]+)/?([\d.]*)", url or "")
    if not m:
        return (url or "")[:120]
    kind, code, version = m.groups()
    if kind == "publicdomain":
        return "CC0" if code == "zero" else "Public domain"
    return f"CC {code.upper()}" + (f" {version}" if version else "")


# ------------------------------------------------------------------ запросы
def _params(query: str, limit: int) -> dict:
    params = {"client_id": settings.jamendo_client_id, "format": "json", "limit": limit, "audioformat": "mp32",
              "order": "popularity_week"}
    tag, text = parse_query(query)
    if tag:
        params["tags"] = tag
    elif text:
        params["search"] = text
        params["order"] = "relevance"
    return params


def _fetch(params: dict) -> list[dict]:
    """Один запрос. Список может быть пустым (в том числе при сбое Jamendo — см. docstring модуля)."""
    with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
        r = client.get(f"{settings.jamendo_api_url}/tracks/", params=params)
        r.raise_for_status()
        body = r.json()
    head = body.get("headers") or {}
    if head.get("status") == "failed":
        if head.get("code") == 5:
            raise JamendoKeyError(str(head.get("error_message", "invalid client id")))
        raise JamendoError(str(head.get("error_message", "request failed")))
    results = []
    for item in body.get("results", []):
        url = item.get("audio")
        if not url or not str(url).startswith(("http://", "https://")):
            continue                              # без прямого потока играть нечего
        results.append({"id": str(item.get("id", "")), "title": str(item.get("name", ""))[:200],
                        "artist": str(item.get("artist_name", ""))[:200], "album": str(item.get("album_name", ""))[:200],
                        "uri": url, "duration": float(item.get("duration") or 0),
                        "license": license_label(str(item.get("license_ccurl", "")))})
    return results


def search(query: str, limit: int = 10) -> list[dict]:
    """Треки по жанру/тексту; без запроса — популярное за неделю. Возвращает словари {id,title,artist,album,uri,duration,license}.
    Пустой ответ повторяется (до ROUNDS пачек по PARALLEL запросов параллельно). JamendoError — сеть или отказ сервиса,
    JamendoKeyError — ключ не принят."""
    if not configured():
        raise JamendoError("jamendo is not configured")
    key = (" ".join((query or "").lower().split()), limit)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_SEC:
        return list(hit[1])

    params = _params(query, limit)
    last_error: Exception | None = None
    answered = False                              # хоть один ответ без ошибки (пусть и пустой)
    for round_no in range(ROUNDS):
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            futures = [pool.submit(_fetch, params) for _ in range(PARALLEL)]
            for fut in as_completed(futures):
                try:
                    results = fut.result()
                except JamendoKeyError:
                    raise
                except (httpx.HTTPError, ValueError, JamendoError) as e:
                    last_error = e
                    continue
                answered = True
                if results:
                    with _cache_lock:
                        _cache[key] = (time.monotonic(), results)
                    for other in futures:
                        other.cancel()
                    return list(results)
        if round_no + 1 < ROUNDS:
            time.sleep(ROUND_PAUSE_SEC)
    if not answered and last_error is not None:
        with _cache_lock:
            stale = _cache.get(key)
        if stale:                                 # сети нет — лучше старый удачный ответ, чем тишина
            return list(stale[1])
        raise JamendoError(str(last_error)) from last_error
    return []


def to_tracks(db: Session, user_id: int, results: list[dict]) -> list[MediaTrack]:
    """Сохраняет найденное в БД (источник jamendo, без дублей по external_id) и возвращает треки."""
    tracks = []
    for r in results:
        track = db.scalar(select(MediaTrack).where(MediaTrack.owner_id == user_id, MediaTrack.source == "jamendo",
                                                   MediaTrack.external_id == r["id"]))
        if track is None:
            track = MediaTrack(owner_id=user_id, source="jamendo", external_id=r["id"])
            db.add(track)
        track.title, track.artist, track.album = r["title"], r["artist"], r["album"]
        track.uri, track.duration_sec, track.license = r["uri"], r["duration"], r["license"]
        tracks.append(track)
    db.commit()
    return tracks
