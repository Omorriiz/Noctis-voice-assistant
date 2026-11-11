"""Недоступные треки: Jamendo (404/410) убирается из кэша и библиотеки; собственные файлы, которых нет на диске, не предлагаются."""
import asyncio
import time

from noctis_server import skills
from noctis_server.db import SessionLocal
from noctis_server.media import decoder, jamendo, library
from noctis_server.media.player import MusicSession, Track
from noctis_server.models import MediaTrack


# ------------------------------------------------------------------ Jamendo: 404 → убрать трек
RESULT = lambda i: {"id": str(i), "title": f"T{i}", "artist": "A", "album": "", "uri": f"https://stream.example/{i}.mp3", "duration": 100.0, "license": "CC BY"}


def test_evict_removes_the_dead_track_from_cached_searches():
    jamendo._cache.clear()
    jamendo._cache[("jazz", 10)] = (time.monotonic(), [RESULT(1), RESULT(2)])
    jamendo._cache[("rock", 10)] = (time.monotonic(), [RESULT(1)])
    jamendo._cache[("pop", 10)] = (time.monotonic(), [RESULT(3)])
    assert jamendo.evict("https://stream.example/1.mp3") == 2
    assert [r["id"] for r in jamendo._cache[("jazz", 10)][1]] == ["2"]
    assert ("rock", 10) not in jamendo._cache                      # остался бы пустой список — ключ убран, следующий поиск сходит в Jamendo
    assert [r["id"] for r in jamendo._cache[("pop", 10)][1]] == ["3"]
    assert jamendo.evict("https://stream.example/none.mp3") == 0
    jamendo._cache.clear()


def test_forget_deletes_the_track_from_the_library(client, auth):
    uid = client.get("/api/v1/me", headers=auth).json()["id"]
    jamendo._cache.clear()
    jamendo._cache[("x", 10)] = (time.monotonic(), [RESULT(7), RESULT(8)])
    with SessionLocal() as db:
        rows = jamendo.to_tracks(db, uid, [RESULT(7), RESULT(8)])
        dead_id = rows[0].id
    jamendo.forget("https://stream.example/7.mp3", dead_id)
    with SessionLocal() as db:
        left = [t.external_id for t in db.query(MediaTrack).filter_by(source="jamendo").all()]
    assert left == ["8"]
    assert [r["id"] for r in jamendo._cache[("x", 10)][1]] == ["8"]
    jamendo._cache.clear()
    jamendo.forget("https://stream.example/never-existed.mp3")       # неизвестный адрес — без ошибки


async def run_to_the_end(session, tracks, index=0):
    """Запускает очередь и ждёт остановки плеера внутри того же цикла событий (иначе его задача снимается вместе с циклом)."""
    await session.play(tracks, index)
    for _ in range(200):
        if session.state == "stopped":
            return
        await asyncio.sleep(0.01)


def make_session():
    sent = []

    async def send(message):
        sent.append(message)
        return True
    return MusicSession("dev", send=send), sent


def test_player_reports_gone_tracks_only_for_404_and_410(monkeypatch):
    def dead(msg):
        def gen():
            raise decoder.DecoderError(msg)
            yield
        return gen()

    messages = {"a": "cannot open stream: HTTP Error 404: Not Found", "b": "cannot open stream: HTTP Error 410: Gone",
                "c": "cannot open stream: HTTP Error 503: Service Unavailable", "d": "cannot open stream: <urlopen error timed out>"}
    monkeypatch.setattr(decoder, "OPEN", lambda uri: dead(messages[uri]))
    gone = []

    async def on_gone(track):
        gone.append(track.uri)

    session, _ = make_session()
    session.on_gone = on_gone
    tracks = [Track(uri, "", "jamendo", uri) for uri in "abcd"]
    asyncio.run(run_to_the_end(session, tracks))
    assert gone == ["a", "b"]                                         # 503 и таймаут — временные сбои, трек не трогаем


def test_sessions_created_by_skills_forget_dead_jamendo_tracks(monkeypatch):
    forgotten = []
    monkeypatch.setattr(jamendo, "forget", lambda uri, track_id=None: forgotten.append((uri, track_id)))

    def dead(uri):
        def gen():
            raise decoder.DecoderError("cannot open stream: HTTP Error 404: Not Found")
            yield
        return gen()

    monkeypatch.setattr(decoder, "OPEN", dead)
    from noctis_server import registry
    registry.players.pop("gone-test", None)
    session = skills.get_session("gone-test")
    assert session.on_gone is not None

    async def send(message):                         # устройства нет, но «плата» принимает сообщения
        return True
    session._send_override = send
    asyncio.run(run_to_the_end(session, [Track("Radio", "", "radio", "http://r/", 5), Track("Gone", "", "jamendo", "https://x/1.mp3", 11)], index=1))
    assert forgotten == [("https://x/1.mp3", 11)]
    registry.players.pop("gone-test", None)


def test_library_drops_tracks_whose_file_is_gone(client, auth, tmp_path, monkeypatch):
    from noctis_server.config import settings
    monkeypatch.setattr(settings, "music_dir", str(tmp_path))
    uid = client.get("/api/v1/me", headers=auth).json()["id"]
    folder = tmp_path / str(uid)
    folder.mkdir()
    (folder / "alive.mp3").write_bytes(b"x")
    with SessionLocal() as db:
        for name in ("alive.mp3", "deleted.mp3"):
            db.add(MediaTrack(owner_id=uid, title=name, source="local", uri=name))
        db.add(MediaTrack(owner_id=uid, title="Radio", source="radio", uri="http://r/"))
        db.commit()
        rows = db.query(MediaTrack).order_by(MediaTrack.id).all()
        kept = library.drop_missing(db, rows)
        assert [t.title for t in kept] == ["alive.mp3", "Radio"]          # радио — не файл, его не трогаем
    with SessionLocal() as db:
        assert sorted(t.title for t in db.query(MediaTrack).all()) == ["Radio", "alive.mp3"]      # запись пропавшего файла удалена и из базы


def test_cancelled_track_does_not_break_the_player(monkeypatch):
    """Трек отменили, пока поток ещё читается в рабочем потоке: закрытие генератора даёт ValueError, плеер не должен падать."""
    import threading
    release = threading.Event()

    def slow(uri):
        release.wait(5)                       # «зависшее» чтение потока
        yield b"\x02\x00" * 3200

    monkeypatch.setattr(decoder, "OPEN", slow)
    sent = []

    async def send(message):
        sent.append(message)
        return True

    async def go():
        session = MusicSession("dev", send=send)
        task = asyncio.create_task(session.play([Track("A", "", "local", "a")]))
        await asyncio.sleep(0.2)
        await session.stop()                  # отмена посреди чтения
        release.set()
        await asyncio.sleep(0.3)
        task.cancel()
        return session

    session = asyncio.run(go())
    assert session.state == "stopped" and session.last_error != "internal"
