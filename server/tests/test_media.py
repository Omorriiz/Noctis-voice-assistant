"""Музыка: библиотека пользователя, Jamendo (мок), радио (локальный поток), плеер, голосовые команды и REST."""
import asyncio
import base64
import functools
import http.server
import math
import socketserver
import struct
import threading
import time
import wave
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from mutagen.id3 import TCON, TIT2, TPE1
from mutagen.wave import WAVE

from noctis_server import registry
from noctis_server import scheduler as sch
from noctis_server.config import settings
from noctis_server.media import decoder, jamendo, library, player
from noctis_server.media.player import MusicSession, Track
from noctis_server.voice import gateway as gw_module

from .conftest import register, run_async
from .helpers import of_type, open_ws, paired, read_until, reply_done, reply_text, say


# ------------------------------------------------------------------ материалы
def wav_bytes(tmp_path, name="x.wav", seconds=1.0, title=None, artist=None, genre=None, rate=16000):
    path = tmp_path / name
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(i / 12))) for i in range(int(rate * seconds))))
    if title:
        tags = WAVE(str(path))
        tags.add_tags()
        tags.tags.add(TIT2(encoding=3, text=title))
        if artist:
            tags.tags.add(TPE1(encoding=3, text=artist))
        if genre:
            tags.tags.add(TCON(encoding=3, text=genre))
        tags.save()
    return path.read_bytes()


@pytest.fixture(autouse=True)
def music_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "music_dir", str(tmp_path / "music"))
    monkeypatch.setattr(settings, "jamendo_client_id", "")
    monkeypatch.setattr(player, "realtime", False)
    monkeypatch.setattr(gw_module, "AFTER_REPLY_DELAY_SEC", 0)
    monkeypatch.setattr(jamendo, "transport", None)


@pytest.fixture
def slow_decoder(monkeypatch):
    """Подмена декодера: 400 кусков по 100 мс (40 с звука), темп — как в жизни (LEAD=0), поэтому поток можно останавливать."""
    opened = []
    monkeypatch.setattr(player, "realtime", True)
    monkeypatch.setattr(player, "LEAD_SEC", 0.0)

    def fake_open(uri):
        opened.append(uri)

        def gen():
            for _ in range(400):
                yield b"\x01\x00" * 1600
        return gen()

    monkeypatch.setattr(decoder, "OPEN", fake_open)
    return opened


def upload(client, auth, tmp_path, name, **tags):
    data = wav_bytes(tmp_path, name, **tags)
    r = client.put(f"/api/v1/media/library/{name}", content=data, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def library3(client, auth, tmp_path):
    ids = [upload(client, auth, tmp_path, "a.wav", title="Утро", artist="Группа Заря", genre="Jazz")["id"],
           upload(client, auth, tmp_path, "b.wav", title="Hotel", artist="Eagles", genre="Rock")["id"],
           upload(client, auth, tmp_path, "c.wav", title="Silence")["id"]]
    return ids


# ------------------------------------------------------------------ библиотека
def test_upload_reads_tags_and_search(client, auth, library3):
    tracks = {t["title"]: t for t in client.get("/api/v1/media/tracks?source=local", headers=auth).json()}
    assert set(tracks) == {"Утро", "Hotel", "Silence"}          # у третьего тегов нет → имя файла в нашем случае тег задан
    assert tracks["Утро"]["artist"] == "Группа Заря" and tracks["Утро"]["genre"] == "Jazz"
    assert tracks["Утро"]["license"] == "own" and 0.9 < tracks["Утро"]["duration_sec"] < 1.1
    q = lambda s: [t["title"] for t in client.get("/api/v1/media/tracks", params={"q": s}, headers=auth).json()]
    assert q("eagles") == ["Hotel"] and q("заря") == ["Утро"] and q("jazz") == ["Утро"] and q("hotel eagles") == ["Hotel"]
    assert q("hotel заря") == [] and q("nothing") == [] and len(q("")) == 3


def test_upload_filename_tag_fallback(client, auth, tmp_path):
    t = upload(client, auth, tmp_path, "My Song.wav")
    assert t["title"] == "My Song" and t["artist"] == ""


def test_upload_validation_and_path_safety(client, auth, tmp_path):
    h = auth
    assert client.put("/api/v1/media/library/virus.exe", content=b"MZ", headers=h).status_code == 422
    assert client.put("/api/v1/media/library/empty.wav", content=b"", headers=h).status_code == 422
    assert client.put("/api/v1/media/library/x.wav", content=b"RIFF", headers=h).status_code == 201   # битый файл не роняет сервер
    r = client.put("/api/v1/media/library/..%5C..%5Cevil.wav", content=wav_bytes(tmp_path, "e.wav"), headers=h)
    assert r.status_code == 201
    root = library.user_dir(1)
    files = {p.name for p in root.rglob("*") if p.is_file()}
    assert "evil.wav" in files and not (root.parent / "evil.wav").exists() and not (root.parent.parent / "evil.wav").exists()
    with pytest.raises(library.LibraryError):
        library.resolve(1, "../2/secret.wav")
    with pytest.raises(library.LibraryError):
        library.safe_filename("noext")
    settings.music_upload_max_mb = 0
    try:
        assert client.put("/api/v1/media/library/big.wav", content=wav_bytes(tmp_path, "big.wav"), headers=h).status_code == 413
    finally:
        settings.music_upload_max_mb = 60
    assert client.put("/api/v1/media/library/a.wav", content=b"x").status_code == 401


def test_scan_adds_updates_and_removes(client, auth, tmp_path):
    root = library.user_dir(1)
    root.mkdir(parents=True)
    (root / "one.wav").write_bytes(wav_bytes(tmp_path, "o.wav", title="One"))
    (root / "sub").mkdir()
    (root / "sub" / "two.wav").write_bytes(wav_bytes(tmp_path, "t.wav", title="Two"))
    (root / "notes.txt").write_text("not music")
    assert client.post("/api/v1/media/library/scan", headers=auth).json() == {"added": 2, "removed": 0, "total": 2}
    assert client.post("/api/v1/media/library/scan", headers=auth).json() == {"added": 0, "removed": 0, "total": 2}
    (root / "one.wav").unlink()
    assert client.post("/api/v1/media/library/scan", headers=auth).json() == {"added": 0, "removed": 1, "total": 1}
    assert [t["title"] for t in client.get("/api/v1/media/tracks", headers=auth).json()] == ["Two"]


def test_library_is_private_per_user_and_delete(client, auth, tmp_path):
    t = upload(client, auth, tmp_path, "mine.wav", title="Mine")
    other = {"Authorization": f"Bearer {register(client, 'b@example.com')['access_token']}"}
    assert client.get("/api/v1/media/tracks", headers=other).json() == []
    assert client.delete(f"/api/v1/media/tracks/{t['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/media/tracks/{t['id']}", headers=auth).status_code == 204
    assert not (library.user_dir(1) / "mine.wav").exists()
    assert client.get("/api/v1/media/tracks", headers=auth).json() == []


def test_sources_are_limited_to_three(client, auth):
    assert client.get("/api/v1/media/tracks?source=youtube", headers=auth).status_code == 422
    from noctis_server.models import MediaTrack
    assert MediaTrack.__table__.c.source.type.length == 16        # local | jamendo | radio (проверяется API; других нет)


# ------------------------------------------------------------------ радио
def test_radio_crud_and_validation(client, auth):
    r = client.post("/api/v1/media/radio", json={"name": "Радио Рекорд", "url": "http://example.org:8000/stream"}, headers=auth)
    assert r.status_code == 201 and r.json()["source"] == "radio" and r.json()["title"] == "Радио Рекорд"
    for bad in ("ftp://example.org/x", "javascript:alert(1)", "file:///etc/passwd", "http://", "not a url"):
        assert client.post("/api/v1/media/radio", json={"name": "X", "url": bad}, headers=auth).status_code == 422, bad
    same = client.post("/api/v1/media/radio", json={"name": "Рекорд 2", "url": "http://example.org:8000/stream"}, headers=auth)
    assert same.json()["id"] == r.json()["id"] and same.json()["title"] == "Рекорд 2"     # тот же URL — обновление, не дубль
    assert len(client.get("/api/v1/media/radio", headers=auth).json()) == 1
    other = {"Authorization": f"Bearer {register(client, 'b@example.com')['access_token']}"}
    assert client.delete(f"/api/v1/media/radio/{r.json()['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/media/radio/{r.json()['id']}", headers=auth).status_code == 204


def test_radio_station_import_file(client, auth, tmp_path):
    from noctis_server.db import SessionLocal
    from noctis_server.media import radio
    f = tmp_path / "stations.json"
    f.write_text('[{"name":"A","url":"http://a.example/s"},{"name":"B"},{"name":"C","url":"gopher://c"}]', encoding="utf-8")
    with SessionLocal() as db:
        assert radio.import_file(db, 1, str(f)) == 1
        assert radio.import_file(db, 1, str(tmp_path / "missing.json")) == 0
        f.write_text("{broken", encoding="utf-8")
        assert radio.import_file(db, 1, str(f)) == 0


# ------------------------------------------------------------------ Jamendo (HTTP-мок)
JAMENDO_BODY = {"headers": {"status": "success"}, "results": [
    {"id": "11", "name": "Smooth Jazz", "artist_name": "Cool Cat", "album_name": "Night", "duration": 187,
     "audio": "https://stream.example/11.mp3", "license_ccurl": "https://creativecommons.org/licenses/by/3.0/"},
    {"id": "12", "name": "No Stream", "artist_name": "X", "audio": ""},
    {"id": "13", "name": "Bad Scheme", "artist_name": "X", "audio": "file:///c:/windows/x.mp3"}]}


def jamendo_on(monkeypatch, handler):
    monkeypatch.setattr(settings, "jamendo_client_id", "test-client")
    monkeypatch.setattr(jamendo, "transport", httpx.MockTransport(handler))


def test_jamendo_not_configured(client, auth):
    assert client.get("/api/v1/media/jamendo/search?q=jazz", headers=auth).status_code == 503


def test_jamendo_search_stores_tracks_without_duplicates(client, auth, monkeypatch):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=JAMENDO_BODY)

    jamendo_on(monkeypatch, handler)
    for _ in range(2):
        r = client.get("/api/v1/media/jamendo/search?q=jazz", headers=auth)
    assert [t["title"] for t in r.json()] == ["Smooth Jazz"]                    # треки без потока/с чужой схемой отброшены
    assert r.json()[0]["license"] == "CC BY 3.0" and r.json()[0]["source"] == "jamendo"
    assert seen[0]["client_id"] == "test-client" and seen[0]["tags"] == "jazz" and seen[0]["format"] == "json"
    assert len(client.get("/api/v1/media/tracks?source=jamendo", headers=auth).json()) == 1


def test_jamendo_failures_are_reported(client, auth, monkeypatch):
    jamendo_on(monkeypatch, lambda request: httpx.Response(500))
    assert client.get("/api/v1/media/jamendo/search?q=x", headers=auth).status_code == 502
    jamendo_on(monkeypatch, lambda request: httpx.Response(200, text="<html>not json</html>"))
    assert client.get("/api/v1/media/jamendo/search?q=x", headers=auth).status_code == 502

    def boom(request):
        raise httpx.ConnectError("no network")
    jamendo_on(monkeypatch, boom)
    assert client.get("/api/v1/media/jamendo/search?q=x", headers=auth).status_code == 502


# ------------------------------------------------------------------ плеер (без сети и платы)
def make_session():
    sent = []

    async def send(message):
        sent.append(message)
        return True

    return MusicSession("dev", send=send), sent


def finite_decoder(monkeypatch, chunks=3):
    monkeypatch.setattr(decoder, "OPEN", lambda uri: iter([b"\x02\x00" * 3200] * chunks))     # 6400 байт = 2 слайса


def types(sent):
    return [m["type"] for m in sent]


def test_player_plays_queue_in_order_and_stops(monkeypatch):
    finite_decoder(monkeypatch)
    session, sent = make_session()
    tracks = [Track("A", "x", "local", "a"), Track("B", "", "local", "b")]

    async def go():
        assert await session.play(tracks)
        for _ in range(100):
            if session.state == "stopped":
                break
            await asyncio.sleep(0.01)
    asyncio.run(go())
    assert types(sent) == (["music_start"] + ["music_chunk"] * 6 + ["music_end"]) * 2
    assert [m["index"] for m in sent if m["type"] == "music_start"] == [0, 1]
    assert sent[1]["index"] == 0 and len(base64.b64decode(sent[1]["data"])) == 3200
    assert session.state == "stopped"


def test_player_pause_resume_next_prev_stop(monkeypatch, slow_decoder):
    session, sent = make_session()
    tracks = [Track("A", "", "local", "a"), Track("B", "", "local", "b"), Track("C", "", "local", "c")]

    async def go():
        assert await session.play(tracks, index=1)
        assert session.info()["track"]["title"] == "B" and session.state == "playing"
        assert await session.pause()
        await asyncio.sleep(0.15)
        n = len(sent)
        await asyncio.sleep(0.4)
        assert len(sent) == n, "во время паузы чанки не идут"
        assert await session.resume() and not await session.resume()
        await asyncio.sleep(0.35)
        assert len(sent) > n
        assert await session.next() and session.info()["track"]["title"] == "C"
        assert await session.next() and session.info()["track"]["title"] == "A"      # по кругу
        assert await session.prev() and session.info()["track"]["title"] == "C"
        await session.stop()
        assert session.state == "stopped"
        n = len(sent)
        await asyncio.sleep(0.2)
        assert len(sent) == n
        assert not await session.pause() and not await session.resume()
    asyncio.run(go())
    kinds = types(sent)
    assert kinds.count("music_start") == 4 and "music_pause" in kinds and "music_resume" in kinds and kinds[-1] == "music_stop"


def test_player_voice_pause_resumes_only_when_paused_by_voice(monkeypatch, slow_decoder):
    session, sent = make_session()

    async def go():
        await session.play([Track("A", "", "local", "a")])
        await session.voice_pause()
        assert session.state == "paused"
        await session.voice_resume()
        assert session.state == "playing"
        await session.pause()                       # пользователь сам поставил паузу
        await session.voice_resume()
        assert session.state == "paused"
        await session.voice_pause()                 # пауза «из-за голоса» поверх ручной не отменяет ручную
        await session.pause()
        await session.voice_resume()
        assert session.state == "paused"
        await session.stop()
    asyncio.run(go())


def test_player_decoder_error_and_offline(monkeypatch):
    def broken(uri):
        raise decoder.DecoderError("bad stream")
        yield

    monkeypatch.setattr(decoder, "OPEN", broken)
    session, sent = make_session()
    assert asyncio.run(session.play([Track("A", "", "radio", "http://x/")])) is False
    assert "bad stream" in session.last_error and types(sent)[-1] == "music_end" and session.state == "stopped"

    async def offline(message):
        return False
    finite_decoder(monkeypatch)
    s2 = MusicSession("dev", send=offline)
    assert asyncio.run(s2.play([Track("A", "", "local", "a")])) is False and s2.last_error == "offline"
    assert asyncio.run(MusicSession("x").play([])) is False


def test_decoder_real_file_and_http_stream(tmp_path):
    wav = tmp_path / "t.wav"
    wav.write_bytes(wav_bytes(tmp_path, "src.wav", seconds=2.0, rate=44100))
    assert sum(len(c) for c in decoder.open_pcm(str(wav))) == 2 * 16000 * 2        # 44,1 кГц → 16 кГц моно
    with pytest.raises(decoder.DecoderError):
        list(decoder.open_pcm(str(tmp_path / "missing.mp3")))
    (tmp_path / "junk.mp3").write_bytes(b"this is not audio" * 10)
    with pytest.raises(decoder.DecoderError):
        list(decoder.open_pcm(str(tmp_path / "junk.mp3")))

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            total = sum(len(c) for c in decoder.open_pcm(f"http://127.0.0.1:{httpd.server_address[1]}/t.wav"))
        finally:
            httpd.shutdown()
    assert total > 60000


def test_radio_station_streams_over_http_through_player(client, auth, tmp_path):
    """Радио целиком: станция в БД → голос «включи радио» → настоящий декодер читает локальный HTTP-поток."""
    (tmp_path / "live.wav").write_bytes(wav_bytes(tmp_path, "src2.wav", seconds=1.0))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{httpd.server_address[1]}/live.wav"
            client.post("/api/v1/media/radio", json={"name": "Тестовое радио", "url": url}, headers=auth)
            pid, token = paired(client, auth)
            ctx, ws, _ = open_ws(client, pid, token)
            try:
                from .conftest import FakeAssistant  # noqa: F401
                assistant = client.app.state.gateway.assistant
                msgs = say(ws, assistant, "Включи радио Тестовое")
                assert reply_text(msgs) == "Включаю радио Тестовое радио."
                msgs = read_until(ws, lambda m: any(x["type"] == "music_end" for x in m))
                assert of_type(msgs, "music_start")[0]["source"] == "radio" if of_type(msgs, "music_start") else True
                got = sum(len(base64.b64decode(m["data"])) for m in of_type(msgs, "music_chunk"))
                assert got > 8000
            finally:
                ctx.__exit__(None, None, None)
        finally:
            httpd.shutdown()


# ------------------------------------------------------------------ голос: музыка
def test_voice_music_flow(client, auth, assistant, library3, slow_decoder):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        msgs = say(ws, assistant, "Включи музыку")
        assert reply_text(msgs) == "Включаю музыку."
        msgs = read_until(ws, lambda m: any(x["type"] == "music_chunk" for x in m))
        start = of_type(msgs, "music_start")[0]
        assert start["total"] == 3 and start["source"] == "local"
        assert client.get(f"/api/v1/devices/{pid}/player", headers=auth).json()["state"] == "playing"

        def kinds_until(wanted):
            return read_until(ws, lambda m: any(x["type"] == wanted for x in m), limit=3000)

        say_until = lambda text, wanted: say(ws, assistant, text, until=lambda m: any(x["type"] == wanted for x in m) and reply_done(m))
        m = say_until("Пауза", "music_pause")
        assert reply_text(m) == "Хорошо."
        assert client.get(f"/api/v1/devices/{pid}/player", headers=auth).json()["state"] == "paused"
        m = say_until("Продолжи", "music_resume")
        assert client.get(f"/api/v1/devices/{pid}/player", headers=auth).json()["state"] == "playing"
        m = say_until("Дальше", "music_start")
        assert of_type(m, "music_start")[-1]["index"] == 1
        m = say_until("Предыдущая песня", "music_start")
        assert of_type(m, "music_start")[-1]["index"] == 0
        m = say_until("Стоп", "music_stop")
        assert reply_text(m) == "Хорошо." and client.get(f"/api/v1/devices/{pid}/player", headers=auth).json()["state"] == "stopped"
        assert not [c for c in assistant.calls if c[0] == "llm"]
    finally:
        ctx.__exit__(None, None, None)


def test_voice_music_query_matches_artist_and_title(client, auth, assistant, library3, slow_decoder):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        assert reply_text(say(ws, assistant, "Включи песню Hotel")) == "Включаю Eagles — Hotel."
        read_until(ws, lambda m: any(x["type"] == "music_chunk" for x in m))
        assert slow_decoder[-1].endswith("b.wav")
        m = say(ws, assistant, "Включи джаз", until=lambda m: reply_done(m))
        assert reply_text(m) == "Включаю Группа Заря — Утро."
    finally:
        ctx.__exit__(None, None, None)


def test_voice_music_polite_failures(client, auth, assistant, tmp_path, monkeypatch):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        assert "библиотеке пока нет музыки" in reply_text(say(ws, assistant, "Включи музыку"))
        assert reply_text(say(ws, assistant, "Включи радио")) == "Радиостанции ещё не добавлены. Добавьте их в приложении."
        assert reply_text(say(ws, assistant, "Включи джаз из Джамендо")) == "Джамендо не настроен на сервере."
        upload(client, auth, tmp_path, "z.wav", title="Zeta")
        assert reply_text(say(ws, assistant, "Включи песню Метал")) == "Не нашёл такой музыки в вашей библиотеке."
        client.post("/api/v1/media/radio", json={"name": "Jazz FM", "url": "http://example.org/jazz"}, headers=auth)
        assert reply_text(say(ws, assistant, "Включи радио Рок")) == "Не нашёл такую радиостанцию."
        jamendo_on(monkeypatch, lambda r: httpx.Response(200, json={"results": []}))
        assert reply_text(say(ws, assistant, "Включи джаз из Джамендо")) == "В Джамендо ничего не нашлось."
        jamendo_on(monkeypatch, lambda r: httpx.Response(503))
        assert reply_text(say(ws, assistant, "Включи джаз из Джамендо")) == "Джамендо сейчас недоступен."
        assert not [c for c in assistant.calls if c[0] == "llm"]
    finally:
        ctx.__exit__(None, None, None)


def test_voice_jamendo_plays_stream(client, auth, assistant, monkeypatch, slow_decoder):
    jamendo_on(monkeypatch, lambda r: httpx.Response(200, json=JAMENDO_BODY))
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        assert reply_text(say(ws, assistant, "Включи джаз из Джамендо")) == "Включаю Cool Cat — Smooth Jazz."
        msgs = read_until(ws, lambda m: any(x["type"] == "music_chunk" for x in m))
        assert of_type(msgs, "music_start")[0]["source"] == "jamendo" and slow_decoder[-1] == "https://stream.example/11.mp3"
    finally:
        ctx.__exit__(None, None, None)


def test_music_pauses_for_a_voice_command_and_resumes_after_reply(client, auth, assistant, library3, slow_decoder):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        say(ws, assistant, "Включи музыку")
        read_until(ws, lambda m: any(x["type"] == "music_chunk" for x in m))
        msgs = say(ws, assistant, "Расскажи про космос", until=lambda m: reply_done(m) and any(x["type"] == "music_resume" for x in m))
        kinds = [m["type"] for m in msgs]
        assert kinds.index("music_pause") < kinds.index("audio_response_start") < kinds.index("music_resume")
        assert [c for c in assistant.calls if c[0] == "llm"]                       # это была обычная реплика
        assert client.get(f"/api/v1/devices/{pid}/player", headers=auth).json()["state"] == "playing"
    finally:
        ctx.__exit__(None, None, None)


def test_music_command_while_voice_paused_keeps_state_consistent(client, auth, assistant, library3, slow_decoder):
    """«Пауза» произносится, когда музыка уже поставлена на паузу самим началом команды: остаётся пауза, не продолжение."""
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        say(ws, assistant, "Включи музыку")
        read_until(ws, lambda m: any(x["type"] == "music_chunk" for x in m))
        say(ws, assistant, "Пауза", until=lambda m: reply_done(m))
        time.sleep(0.2)
        assert client.get(f"/api/v1/devices/{pid}/player", headers=auth).json()["state"] == "paused"
    finally:
        ctx.__exit__(None, None, None)


def test_alarm_pauses_music(client, auth, assistant, library3, slow_decoder):
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    gw = client.app.state.gateway
    try:
        say(ws, assistant, "Включи музыку")
        read_until(ws, lambda m: any(x["type"] == "music_chunk" for x in m))
        client.post(f"/api/v1/devices/{pid}/timers", json={"seconds": 30}, headers=auth)
        fired = run_async(client, gw.scheduler.tick, datetime.now(timezone.utc) + timedelta(seconds=60))
        assert len(fired) == 1
        msgs = read_until(ws, lambda m: reply_done(m) and any(x["type"] == "music_pause" for x in m), limit=3000)
        assert reply_text(msgs) == "Таймер завершён."
        assert client.get(f"/api/v1/devices/{pid}/player", headers=auth).json()["state"] == "paused"
    finally:
        ctx.__exit__(None, None, None)


# ------------------------------------------------------------------ REST: плеер и плейлисты
def test_player_rest_controls(client, auth, library3, slow_decoder):
    pid, token = paired(client, auth)
    assert client.post(f"/api/v1/devices/{pid}/player/play", json={"track_id": library3[0]}, headers=auth).status_code == 409   # офлайн
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        assert client.get(f"/api/v1/devices/{pid}/player", headers=auth).json()["state"] == "stopped"
        assert client.post(f"/api/v1/devices/{pid}/player/pause", headers=auth).status_code == 409        # нечего ставить на паузу
        r = client.post(f"/api/v1/devices/{pid}/player/play", json={"query": "eagles"}, headers=auth)
        assert r.status_code == 200 and r.json()["track"]["title"] == "Hotel" and r.json()["state"] == "playing"
        assert client.post(f"/api/v1/devices/{pid}/player/pause", headers=auth).json()["state"] == "paused"
        assert client.post(f"/api/v1/devices/{pid}/player/resume", headers=auth).json()["state"] == "playing"
        assert client.post(f"/api/v1/devices/{pid}/player/stop", headers=auth).json()["state"] == "stopped"
        assert client.post(f"/api/v1/devices/{pid}/player/dance", headers=auth).status_code == 404
        assert client.post(f"/api/v1/devices/{pid}/player/play", json={"query": "nothing-like-this"}, headers=auth).status_code == 404
        assert client.post(f"/api/v1/devices/{pid}/player/play", json={"track_id": 99999}, headers=auth).status_code == 404
        assert client.post(f"/api/v1/devices/{pid}/player/play", json={"source": "jamendo"}, headers=auth).status_code == 503
        assert client.post(f"/api/v1/devices/{pid}/player/play", json={"source": "radio", "query": "x"}, headers=auth).status_code == 404
    finally:
        ctx.__exit__(None, None, None)


def test_player_rest_is_per_owner(client, auth, library3, slow_decoder):
    pid, token = paired(client, auth)
    other = {"Authorization": f"Bearer {register(client, 'b@example.com')['access_token']}"}
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        assert client.get(f"/api/v1/devices/{pid}/player", headers=other).status_code == 404
        assert client.post(f"/api/v1/devices/{pid}/player/play", json={"track_id": library3[0]}, headers=other).status_code == 404
    finally:
        ctx.__exit__(None, None, None)


def test_playlists(client, auth, library3, slow_decoder):
    pl = client.post("/api/v1/media/playlists", json={"name": "Вечер"}, headers=auth).json()
    r = client.put(f"/api/v1/media/playlists/{pl['id']}/items", json={"track_ids": [library3[2], library3[0]]}, headers=auth)
    assert r.json()["track_ids"] == [library3[2], library3[0]]
    assert client.put(f"/api/v1/media/playlists/{pl['id']}/items", json={"track_ids": [999]}, headers=auth).status_code == 404
    assert client.get("/api/v1/media/playlists", headers=auth).json()[0]["name"] == "Вечер"
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        r = client.post(f"/api/v1/devices/{pid}/player/play", json={"playlist_id": pl["id"]}, headers=auth)
        assert r.json()["total"] == 2 and r.json()["track"]["title"] == "Silence"
    finally:
        ctx.__exit__(None, None, None)
    other = {"Authorization": f"Bearer {register(client, 'b@example.com')['access_token']}"}
    assert client.delete(f"/api/v1/media/playlists/{pl['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/media/playlists/{pl['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/media/tracks", headers=auth).json()          # треки остались


# ------------------------------------------------------------------ сетевой источник декодера
def raw_http_server(response: bytes):
    """Минимальный TCP-сервер: читает запрос, отдаёт готовый ответ и закрывает соединение (поток закончился)."""
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(response)
        conn.close()
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return f"http://127.0.0.1:{srv.getsockname()[1]}/stream"


def test_http_source_strips_icy_metadata_and_ends_cleanly():
    payload = bytes(range(256)) * 80
    metaint = 4096
    body = b""
    for i in range(0, len(payload), metaint):
        body += payload[i:i + metaint] + (b"\x01" + b"StreamTitle='x';".ljust(16, b"\0") if i == 0 else b"\x00")
    url = raw_http_server(b"HTTP/1.0 200 OK\r\nContent-Type: audio/mpeg\r\nicy-metaint: 4096\r\n\r\n" + body)
    src = decoder.make_http_source(url)
    try:
        got = b""
        t0 = time.monotonic()
        while True:
            part = src.read(1000)
            got += part
            if len(part) < 1000:
                break
        assert got == payload and time.monotonic() - t0 < 5
        assert src.read(1000) == b""                      # после конца потока — пустые данные, а не зависание
    finally:
        src.close()


def test_http_source_rejects_aac_and_dead_hosts():
    url = raw_http_server(b"HTTP/1.0 200 OK\r\nContent-Type: audio/aacp\r\n\r\nxxxx")
    with pytest.raises(decoder.DecoderError, match="AAC"):
        decoder.make_http_source(url)
    with pytest.raises(decoder.DecoderError, match="cannot open"):
        decoder.make_http_source("http://127.0.0.1:1/none")
    with pytest.raises(decoder.DecoderError):
        list(decoder.open_pcm("http://127.0.0.1:1/none"))


# ------------------------------------------------------------------ MP3 (основной формат Jamendo и радио)
@pytest.fixture
def mp3_bytes():
    lameenc = pytest.importorskip("lameenc")
    enc = lameenc.Encoder()
    enc.set_bit_rate(128)
    enc.set_in_sample_rate(44100)
    enc.set_channels(2)
    enc.set_quality(7)
    pcm = b"".join(struct.pack("<hh", int(9000 * math.sin(i / 14)), int(9000 * math.sin(i / 11))) for i in range(44100 * 2))
    return bytes(enc.encode(pcm) + enc.flush())


def test_decoder_reads_mp3_file_with_resampling(tmp_path, mp3_bytes):
    path = tmp_path / "t.mp3"
    path.write_bytes(mp3_bytes)
    total = sum(len(c) for c in decoder.open_pcm(str(path)))
    assert 0.9 * 64000 < total < 1.1 * 64000                         # 2 с, 16 кГц, 16 бит, моно


def test_mp3_tags_and_upload(client, auth, tmp_path, mp3_bytes):
    from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1
    path = tmp_path / "tagged.mp3"
    path.write_bytes(mp3_bytes)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Ночной город"))
    tags.add(TPE1(encoding=3, text="Группа"))
    tags.add(TALB(encoding=3, text="Альбом"))
    tags.add(TCON(encoding=3, text="Rock"))
    tags.save(str(path))
    r = client.put("/api/v1/media/library/tagged.mp3", content=path.read_bytes(), headers=auth)
    assert r.status_code == 201
    t = r.json()
    assert (t["title"], t["artist"], t["album"], t["genre"]) == ("Ночной город", "Группа", "Альбом", "Rock")
    assert 1.5 < t["duration_sec"] < 2.6
    found = client.get("/api/v1/media/tracks", params={"q": "рок"}, headers=auth).json()
    assert [x["title"] for x in found] == ["Ночной город"]           # «рок» находит жанр Rock


def test_mp3_radio_stream_with_icy_metadata_decodes(mp3_bytes):
    """Радиопоток MP3 с ICY-метаданными целиком: HTTP → HttpSource (снятие метаданных) → miniaudio → PCM 16 кГц."""
    metaint = 4096
    body = b""
    for i in range(0, len(mp3_bytes), metaint):
        body += mp3_bytes[i:i + metaint]
        if len(mp3_bytes[i:i + metaint]) == metaint:
            body += b"\x01" + b"StreamTitle='x';".ljust(16, b"\0")
    url = raw_http_server(b"HTTP/1.0 200 OK\r\nContent-Type: audio/mpeg\r\nicy-metaint: 4096\r\n\r\n" + body)
    total = sum(len(c) for c in decoder.open_pcm(url))
    assert 0.9 * 64000 < total < 1.1 * 64000


def test_radio_import_endpoint(client, auth, tmp_path, monkeypatch):
    f = tmp_path / "stations.json"
    f.write_text('[{"name": "Jazz FM", "url": "http://a.example/jazz"}, {"name": "Rock FM", "url": "https://b.example/rock"}]',
                 encoding="utf-8")
    monkeypatch.setattr(settings, "radio_stations_file", str(f))
    assert client.post("/api/v1/media/radio/import", headers=auth).json() == {"added": 2}
    assert [t["title"] for t in client.get("/api/v1/media/radio", headers=auth).json()] == ["Jazz FM", "Rock FM"]
    assert client.post("/api/v1/media/radio/import", headers=auth).json() == {"added": 2}      # повторно — без дублей
    assert len(client.get("/api/v1/media/radio", headers=auth).json()) == 2
    assert client.post("/api/v1/media/radio/import").status_code == 401
