"""Музыкальный плеер устройства: очередь треков, поток PCM16 16 кГц на плату в реальном времени.

Протокол (сервер → плата):
  music_start {title, artist, source, index, total}   — начало трека (плата сбрасывает буфер)
  music_chunk {index, data(base64 PCM16 16 кГц моно)}  — 3200 байт = 100 мс; сервер опережает воспроизведение на LEAD_SEC
  music_end {reason}                                   — трек закончился («end»/«error»)
  music_pause / music_resume / music_stop              — управление (плата сразу глушит/сбрасывает буфер)
Плата не декодирует MP3 — декодирует сервер (media/decoder.py).
"""
import asyncio
import base64
import logging
import time
from dataclasses import dataclass

from .. import registry
from . import decoder

log = logging.getLogger("noctis.player")

CHUNK_BYTES = 3200                 # 100 мс
BYTES_PER_SEC = 32000
LEAD_SEC = 6.0                     # на столько секунд сервер опережает воспроизведение (буфер платы 12 с): рывки Wi-Fi не слышны
START_TIMEOUT_SEC = 10.0
realtime = True                    # в тестах выключается (без пауз между чанками)


@dataclass
class Track:
    title: str
    artist: str
    source: str                    # local | jamendo | radio
    uri: str                       # путь к файлу или URL потока
    track_id: int | None = None

    @property
    def live(self) -> bool:
        return self.source == "radio"

    def label(self) -> str:
        return f"{self.artist} — {self.title}" if self.artist else self.title


class MusicSession:
    def __init__(self, public_id: str, send=None):
        self.public_id = public_id
        self._send_override = send
        self.state = "stopped"             # stopped | playing | paused
        self.queue: list[Track] = []
        self.index = 0
        self.last_error = ""
        self._task: asyncio.Task | None = None
        self._gen = 0
        self._playing = asyncio.Event()
        self._started: asyncio.Future | None = None
        self._voice_paused = False

    # ---------- отправка ----------
    async def _send(self, message: dict) -> bool:
        if self._send_override:
            return await self._send_override(message)
        conn = registry.online_devices.get(self.public_id)
        return bool(conn) and await conn.send_json(message)

    # ---------- состояние ----------
    @property
    def current(self) -> Track | None:
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def info(self) -> dict:
        t = self.current
        return {"state": self.state, "index": self.index, "total": len(self.queue),
                "track": ({"title": t.title, "artist": t.artist, "source": t.source, "id": t.track_id} if t else None)}

    # ---------- управление ----------
    async def play(self, tracks: list[Track], index: int = 0) -> bool:
        """Запускает очередь. True — первый звук ушёл на плату; False — не удалось (см. last_error)."""
        await self._cancel_task()
        if not tracks:
            return False
        self.queue, self.index, self.last_error = list(tracks), max(0, min(index, len(tracks) - 1)), ""
        return await self._start_current()

    async def _start_current(self) -> bool:
        self._gen += 1
        log.debug("[%s] старт трека %d/%d gen=%d", self.public_id, self.index, len(self.queue), self._gen)
        self.state, self._voice_paused = "playing", False
        self._playing.set()
        self._started = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._run(self._gen))
        try:
            return await asyncio.wait_for(asyncio.shield(self._started), START_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            log.warning("[%s] первый звук не ушёл за %s с", self.public_id, START_TIMEOUT_SEC)
            self.last_error = "timeout"
            await self.stop()
            return False

    async def _cancel_task(self):
        log.debug("[%s] отмена потока gen=%d", self.public_id, self._gen)
        self._gen += 1
        task, self._task = self._task, None
        self._playing.set()                # разбудить ожидание паузы
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def stop(self) -> None:
        was_active = self.state != "stopped"
        await self._cancel_task()
        self.state, self._voice_paused = "stopped", False
        if was_active:
            await self._send({"type": "music_stop"})

    async def pause(self, by_voice: bool = False) -> bool:
        if self.state == "paused":                 # уже на паузе (например, из-за начала команды): теперь пауза «насовсем»
            if not by_voice:
                self._voice_paused = False
            return True
        if self.state != "playing":
            return False
        self.state = "paused"
        self._voice_paused = by_voice
        self._playing.clear()
        await self._send({"type": "music_pause"})
        return True

    async def resume(self) -> bool:
        if self.state != "paused":
            return False
        self.state, self._voice_paused = "playing", False
        self._playing.set()
        await self._send({"type": "music_resume"})
        return True

    async def voice_pause(self):
        """Пользователь начал говорить с ассистентом: музыку на паузу (вернём после ответа)."""
        await self.pause(by_voice=True)

    async def voice_resume(self):
        if self.state == "paused" and self._voice_paused:
            await self.resume()

    async def next(self) -> bool:
        if not self.queue:
            return False
        self.index = (self.index + 1) % len(self.queue)
        await self._cancel_task()
        return await self._start_current()

    async def prev(self) -> bool:
        if not self.queue:
            return False
        self.index = (self.index - 1) % len(self.queue)
        await self._cancel_task()
        return await self._start_current()

    # ---------- поток ----------
    def _resolve_started(self, ok: bool):
        if self._started and not self._started.done():
            self._started.set_result(ok)

    async def _run(self, gen: int):
        try:
            while gen == self._gen:
                track = self.current
                if track is None:
                    break
                reason = await self._stream(track, gen)
                if gen != self._gen:
                    return
                await self._send({"type": "music_end", "reason": reason})
                if reason == "error" or track.live or self.index + 1 >= len(self.queue):
                    break                          # ошибка, конец радиопотока или очереди
                self.index += 1
            if gen == self._gen:
                self.state = "stopped"
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[%s] ошибка плеера", self.public_id)
            self.last_error = "internal"
        finally:
            self._resolve_started(False)           # если ни один звук не ушёл

    async def _stream(self, track: Track, gen: int) -> str:
        if not await self._send({"type": "music_start", "title": track.title[:60], "artist": track.artist[:60],
                                 "source": track.source, "index": self.index, "total": len(self.queue)}):
            self.last_error = "offline"
            return "error"
        it = decoder.OPEN(track.uri)
        t0, sent, idx = time.monotonic(), 0, 0
        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(next, it, None)
                except decoder.DecoderError as e:
                    self.last_error = str(e)
                    log.warning("[%s] не удалось декодировать %s: %s", self.public_id, track.title, e)
                    return "error"
                if chunk is None:
                    return "end"
                for off in range(0, len(chunk), CHUNK_BYTES):
                    if gen != self._gen:
                        return "cancelled"
                    if not self._playing.is_set():
                        paused_at = time.monotonic()
                        await self._playing.wait()
                        t0 += time.monotonic() - paused_at       # время паузы не считается воспроизведённым
                        if gen != self._gen:
                            return "cancelled"
                    part = chunk[off:off + CHUNK_BYTES]
                    if not await self._send({"type": "music_chunk", "index": idx,
                                             "data": base64.b64encode(part).decode()}):
                        self.last_error = "offline"
                        self.state = "paused"             # плата отключилась: ждём ручного продолжения
                        self._playing.clear()
                        return "error"
                    idx += 1
                    sent += len(part)
                    self._resolve_started(True)
                    if realtime:
                        delay = t0 + sent / BYTES_PER_SEC - LEAD_SEC - time.monotonic()
                        if delay > 0:
                            await asyncio.sleep(delay)
        finally:
            close = getattr(it, "close", None)
            if close:
                close()
