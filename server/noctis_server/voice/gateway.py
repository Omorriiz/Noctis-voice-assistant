"""WebSocket-шлюз устройств Noctis (ESP32).

Протокол совместим с прежней прошивкой:
  → start_recording, audio_chunk {index, data(base64)}, stop_recording, ping
  ← hello, text_response, audio_response_start, audio_response_chunk, audio_response_end, pong
Новое:
  ← config {wake_word, voice_gender, ...}  — настройки устройства (при подключении и при изменении)
  ← command {name}                          — команды из приложения (reboot)

Аутентификация: ?device_id=<public_id>&token=<секрет> (или заголовки X-Device-Id / X-Device-Token).
Без токена пускаем только если NOCTIS_ALLOW_ANONYMOUS_DEVICES=true (режим совместимости).
"""
import asyncio
import base64
import hmac
import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from starlette.websockets import WebSocketState

from .. import intents, registry, skills
from .. import personalization as pz
from .. import scheduler as sch
from ..config import settings
from ..db import SessionLocal
from ..models import Device, DeviceSettings, utcnow
from ..schemas import DeviceSettingsOut
from ..security import sha256
from ..services import config_payload, save_history

logger = logging.getLogger("noctis.gateway")

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
SERVER_AUDIO_CHUNK_BYTES = 2048
CHUNK_DELAY_SEC = 0.005   # минимальная пауза между чанками (уступить цикл событий)
TTS_LEAD_SEC = 5.0        # на столько секунд голос опережает воспроизведение (буфер платы 12 с): рывки Wi-Fi не слышны
BYTES_PER_SEC = SAMPLE_RATE * SAMPLE_WIDTH
MAX_AUDIO_BYTES = 40 * SAMPLE_RATE * SAMPLE_WIDTH
MIN_AUDIO_BYTES = 8000
PENDING_TTL_SEC = 180
MAX_HISTORY_PAIRS = 5
AFTER_REPLY_DELAY_SEC = 0.5      # пауза между ответом-подтверждением и запуском музыки
VOICE_PAUSE_MAX_SEC = 45         # музыка, поставленная на паузу голосом, сама продолжится, если команды не было

# Служебные фразы берутся на языке устройства: pz.phrase(ключ, настройки)


class DeviceConnection:
    """Состояние одного подключённого устройства."""

    def __init__(self, ws: WebSocket, conn_id: str, key: str, device_id: int | None,
                 device_settings: dict):
        self.ws = ws
        self.id = conn_id
        self.key = key                      # public_id устройства или anon-<ip>
        self.device_id = device_id          # None для анонимных
        self.settings = device_settings     # актуальный снимок настроек
        self.host = ws.client.host if ws.client else "unknown"
        self.audio_buffer = bytearray()
        self.is_recording = False
        self.connected_at = datetime.now()
        self.chunk_count = 0
        self.total_audio_bytes = 0
        self.processing_task: asyncio.Task | None = None
        self.history: deque = deque(maxlen=MAX_HISTORY_PAIRS * 2)  # у каждого устройства своя

    def websocket_open(self) -> bool:
        try:
            return (self.ws.client_state == WebSocketState.CONNECTED
                    and self.ws.application_state == WebSocketState.CONNECTED)
        except Exception:
            return False

    # ---------- запись ----------
    def start_recording(self):
        self.is_recording = True
        self.audio_buffer = bytearray()
        self.chunk_count = 0
        self.total_audio_bytes = 0

    def add_audio(self, chunk: bytes):
        if not self.is_recording:
            return
        if self.total_audio_bytes + len(chunk) > MAX_AUDIO_BYTES:
            self.audio_buffer = bytearray()
            self.is_recording = False
            raise RuntimeError("Превышен лимит входящего аудио")
        self.chunk_count += 1
        self.total_audio_bytes += len(chunk)
        self.audio_buffer.extend(chunk)

    def finish_recording(self) -> bytes:
        if not self.is_recording and not self.audio_buffer:
            raise RuntimeError("Нет активной записи")
        audio = bytes(self.audio_buffer)
        self.audio_buffer = bytearray()
        self.is_recording = False
        return audio

    # ---------- отправка ----------
    async def send_json(self, payload: dict) -> bool:
        if not self.websocket_open():
            return False
        try:
            await self.ws.send_json(payload)
            return True
        except Exception as e:
            # Обрыв соединения во время ответа — обычное дело (Wi-Fi платы): ответ откладывается до переподключения
            logger.warning("[%s] не удалось отправить JSON (%s: %s)", self.id, type(e).__name__, e or "соединение закрыто")
            return False

    async def send_text(self, text: str) -> bool:
        return await self.send_json({"type": "text_response", "text": text})

    async def push_config(self, config: dict):
        self.settings = config
        await self.send_json({"type": "config", **config})

    async def send_audio(self, audio_bytes: bytes) -> bool:
        if not audio_bytes:
            return False
        total_chunks = (len(audio_bytes) + SERVER_AUDIO_CHUNK_BYTES - 1) // SERVER_AUDIO_CHUNK_BYTES
        if not await self.send_json({"type": "audio_response_start", "format": "pcm16",
                                     "sample_rate": SAMPLE_RATE, "total_bytes": len(audio_bytes),
                                     "chunk_bytes": SERVER_AUDIO_CHUNK_BYTES, "total_chunks": total_chunks}):
            return False
        pacer = Pacer()
        for index in range(total_chunks):
            chunk = audio_bytes[index * SERVER_AUDIO_CHUNK_BYTES:(index + 1) * SERVER_AUDIO_CHUNK_BYTES]
            if not await self.send_json({"type": "audio_response_chunk", "index": index,
                                         "data": base64.b64encode(chunk).decode()}):
                return False
            await pacer.wait(len(chunk))
        return await self.send_json({"type": "audio_response_end", "total_chunks": total_chunks})


class Pacer:
    """Отправка звука с опережением: не дальше TTS_LEAD_SEC от того, что плата уже проиграла.
    Если плата «догнала» (предложение синтезируется медленнее речи), опережение считается от текущего момента."""

    def __init__(self):
        self.play_end = 0.0   # когда плата доиграет всё отправленное

    async def wait(self, nbytes: int):
        now = time.monotonic()
        self.play_end = max(self.play_end, now) + nbytes / BYTES_PER_SEC
        await asyncio.sleep(max(self.play_end - now - TTS_LEAD_SEC, CHUNK_DELAY_SEC))


class StreamingReply:
    """Ответ по мере синтеза: start → чанки каждого предложения → end. При обрыве связи
    весь ответ целиком откладывается и доставляется после переподключения."""

    def __init__(self, gw: "Gateway", conn: DeviceConnection):
        self.gw, self.conn = gw, conn
        self.target = gw.live_connection(conn.key)
        self.failed = self.target is None
        self.started = False
        self.index = 0
        self.audio = bytearray()
        self.first_audio_at: float | None = None
        self.pacer = Pacer()

    async def add(self, pcm: bytes):
        self.audio += pcm
        if self.failed:
            return
        if not self.started:
            if not await self.target.send_json({"type": "audio_response_start", "format": "pcm16",
                                                "sample_rate": SAMPLE_RATE, "streaming": True}):
                self.failed = True
                return
            self.started = True
            self.first_audio_at = time.time()
        for offset in range(0, len(pcm), SERVER_AUDIO_CHUNK_BYTES):
            if not await self.target.send_json({
                    "type": "audio_response_chunk", "index": self.index,
                    "data": base64.b64encode(pcm[offset:offset + SERVER_AUDIO_CHUNK_BYTES]).decode()}):
                self.failed = True
                return
            self.index += 1
            await self.pacer.wait(min(SERVER_AUDIO_CHUNK_BYTES, len(pcm) - offset))

    async def finish(self, text: str):
        if not self.failed and self.started:
            if await self.target.send_json({"type": "audio_response_end", "total_chunks": self.index}):
                await self.target.send_text(text)
                return
        if self.audio:
            await self.gw.deliver(self.conn, text, bytes(self.audio))
        else:
            await self.gw.speak(self.conn, pz.phrase("error", self.conn.settings))

    async def abort(self):
        if self.started and not self.failed:
            await self.target.send_json({"type": "audio_response_end", "total_chunks": self.index})


class Gateway:
    def __init__(self, assistant):
        self.assistant = assistant  # объект с transcribe / ask_llm_stream / tts_pcm16
        self.active: dict[str, DeviceConnection] = {}
        self.pending: dict[str, dict] = {}
        self.pipeline_lock = asyncio.Lock()  # GPU/модели не рассчитаны на параллельные запросы
        self.tasks: set[asyncio.Task] = set()
        self.scheduler = sch.SchedulerService(self, self._notify_fired)

    # ---------- утилиты ----------
    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def live_connection(self, key: str) -> DeviceConnection | None:
        candidates = [c for c in self.active.values() if c.key == key and c.websocket_open()]
        return max(candidates, key=lambda c: c.connected_at, default=None)

    async def synthesize_pcm(self, conn: DeviceConnection, text: str) -> bytes:
        return await asyncio.to_thread(self.assistant.tts_pcm16, text, conn.settings)

    async def deliver(self, conn: DeviceConnection, text: str, audio: bytes):
        target = self.live_connection(conn.key)
        if target is not None:
            await target.send_text(text)
            if await target.send_audio(audio):
                return
        logger.warning("[%s] ответ не доставлен, отложен до переподключения", conn.id)
        self.pending[conn.key] = {"time": time.time(), "text": text, "audio": audio}

    async def deliver_pending(self, conn: DeviceConnection):
        pending = self.pending.pop(conn.key, None)
        if pending is None or time.time() - pending["time"] > PENDING_TTL_SEC:
            return
        await conn.send_text(pending["text"])
        if not await conn.send_audio(pending["audio"]):
            self.pending[conn.key] = pending

    async def speak(self, conn: DeviceConnection, text: str):
        """Короткая служебная фраза; при сбое TTS хотя бы закрываем ответ, чтобы ESP32 не ждал таймаут."""
        try:
            await self.deliver(conn, text, await self.synthesize_pcm(conn, text))
        except Exception as e:
            logger.error("[%s] не удалось озвучить «%s»: %s", conn.id, text, e)
            target = self.live_connection(conn.key)
            if target is not None:
                await target.send_text(text)
                await target.send_json({"type": "audio_response_end", "total_chunks": 0})

    # ---------- LLM → TTS → устройство ----------
    async def stream_answer(self, conn: DeviceConnection, user_text: str):
        loop = asyncio.get_running_loop()
        sentences: asyncio.Queue = asyncio.Queue()
        audio_queue: asyncio.Queue = asyncio.Queue()
        reply = StreamingReply(self, conn)
        spoken: list[str] = []
        timing = {"llm": 0.0, "tts": 0.0}
        t0 = time.time()

        def produce():
            try:
                for sentence in self.assistant.ask_llm_stream(user_text, conn.history, conn.settings):
                    loop.call_soon_threadsafe(sentences.put_nowait, sentence)
            finally:
                timing["llm"] = time.time() - t0
                loop.call_soon_threadsafe(sentences.put_nowait, None)

        async def synth():
            try:
                while (sentence := await sentences.get()) is not None:
                    spoken.append(sentence)
                    t = time.time()
                    try:
                        pcm = await self.synthesize_pcm(conn, sentence)
                    except Exception as e:
                        logger.error("[%s] TTS пропустил предложение: %s", conn.id, e)
                        continue
                    timing["tts"] += time.time() - t
                    await audio_queue.put(pcm)
            finally:
                await audio_queue.put(None)

        async def send():
            while (pcm := await audio_queue.get()) is not None:
                await reply.add(pcm)

        try:
            await asyncio.gather(asyncio.to_thread(produce), synth(), send())
        except Exception:
            await reply.abort()
            raise
        answer = " ".join(spoken)
        await reply.finish(answer)
        return answer, reply, timing

    async def process_audio(self, conn: DeviceConnection):
        t_start = time.time()
        try:
            audio = conn.finish_recording()
            async with self.pipeline_lock:
                if len(audio) < MIN_AUDIO_BYTES:
                    await self.speak(conn, pz.phrase("too_short", conn.settings))
                    return
                audio_np = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
                if settings.store_audio:  # только для отладки; по умолчанию голос не сохраняется
                    import soundfile as sf
                    from pathlib import Path
                    out = Path(settings.output_dir)
                    out.mkdir(parents=True, exist_ok=True)
                    sf.write(str(out / f"{conn.id}_input.wav"), audio_np, SAMPLE_RATE, subtype="PCM_16")

                t0 = time.time()
                user_text = await asyncio.to_thread(self.assistant.transcribe, audio_np, conn.settings)
                t_stt = time.time() - t0
                if not user_text:
                    await self.speak(conn, pz.phrase("not_heard", conn.settings))
                    return
                target = self.live_connection(conn.key)
                if target:
                    await target.send_text(f"Вы сказали: {user_text}")

                if await self.try_skill(conn, user_text, t_stt):
                    return
                answer, reply, timing = await self.stream_answer(conn, user_text)
                timing["stt"] = t_stt
                self._remember(conn, user_text, answer, timing)

            first = f"{reply.first_audio_at - t_start:.1f} с" if reply.first_audio_at else "—"
            logger.info("[%s] цикл %.1f с, первый звук %s (STT %.1f, LLM %.1f, TTS %.1f)", conn.id,
                        time.time() - t_start, first, t_stt, timing["llm"], timing["tts"])
        except Exception as e:
            logger.exception("[%s] ошибка process_audio: %s", conn.id, e)
            await self.speak(conn, pz.phrase("error", conn.settings))
        finally:
            conn.processing_task = None
            session = registry.players.get(conn.key)
            if session is not None:
                await session.voice_resume()      # команда была не про музыку — музыка продолжается

    # ---------- команды без LLM: время, таймеры, будильники, музыка ----------
    async def try_skill(self, conn: DeviceConnection, user_text: str, t_stt: float) -> bool:
        if conn.device_id is None:               # анонимные устройства (режим совместимости) — только диалог
            return False
        intent = intents.parse(user_text, pz.normalize(conn.settings)["language"])
        if intent is None:
            return False
        with SessionLocal() as db:
            device = db.get(Device, conn.device_id)
            if device is None:
                return False
            ctx = skills.Context(db, device, conn.settings, push_config=conn.push_config)
            result = await skills.run(intent, ctx)
        if result is None:
            return False
        logger.info("[%s] команда %s", conn.id, intent.kind)
        await self.speak(conn, result.text)
        if result.action is not None:
            await asyncio.sleep(AFTER_REPLY_DELAY_SEC)
            await result.action()
        self._remember(conn, user_text, result.text, {"stt": t_stt})
        return True

    async def _notify_fired(self, conn: DeviceConnection, fired: sch.Fired):
        session = registry.players.get(conn.key)
        if session is not None and session.state == "playing":
            await session.pause()
        async with self.pipeline_lock:
            await self.speak(conn, skills.fired_phrase(fired))

    async def _on_sched_event(self, conn: DeviceConnection, message: dict):
        try:
            kind, item_id = str(message.get("kind", ""))[:8], int(message.get("id", 0))
        except (TypeError, ValueError):
            return
        with SessionLocal() as db:
            device = db.get(Device, conn.device_id) if conn.device_id else None
            fired = sch.handle_device_event(db, device, kind, item_id) if device else None
        if fired:
            await self.scheduler.deliver(fired)

    async def _resume_music_later(self, conn: DeviceConnection, session):
        await asyncio.sleep(VOICE_PAUSE_MAX_SEC)
        if conn.processing_task is None:
            await session.voice_resume()

    def _remember(self, conn: DeviceConnection, user_text: str, answer: str, timing: dict):
        if conn.device_id is None:
            return
        with SessionLocal() as db:
            device = db.get(Device, conn.device_id)
            if device:
                save_history(db, device, user_text, answer, timing)

    # ---------- служебные сообщения прошивки ----------
    def _on_device_info(self, conn: DeviceConnection, message: dict):
        fw = str(message.get("firmware", ""))[:32]
        logger.info("[%s] device_info: fw=%s rev=%s reset=%s", conn.id, fw, message.get("config_revision"),
                    message.get("reset_reason"))
        if conn.device_id is None:
            return
        with SessionLocal() as db:
            device = db.get(Device, conn.device_id)
            if device:
                device.firmware_version = fw
                device.last_seen = utcnow()
                db.commit()

    def _on_ota_status(self, conn: DeviceConnection, message: dict):
        status = {"version": str(message.get("version", ""))[:16], "state": str(message.get("state", ""))[:16],
                  "error": str(message.get("error", ""))[:80]}
        logger.info("[%s] ota_status: %s", conn.id, status)
        registry.ota_status[conn.key] = status

    def _on_wifi_result(self, conn: DeviceConnection, message: dict):
        """Итог смены сети: ok — колонка в новой сети и на связи; failed — вернулась в прежнюю. Пароль сюда не приходит."""
        ch = registry.wifi_changes.get(conn.key)
        if ch is None or str(message.get("request_id", "")) != ch["request_id"]:
            return
        ch["state"] = "ok" if message.get("state") == "ok" else "failed"
        ch["error"] = str(message.get("error", ""))[:40]
        logger.info("[%s] wifi_result: %s %s", conn.id, ch["state"], ch["error"])

    # ---------- аутентификация ----------
    def authenticate(self, ws: WebSocket) -> tuple[str, int | None, dict] | None:
        """Возвращает (ключ, device_pk | None, настройки) или None, если доступ запрещён."""
        device_id = ws.query_params.get("device_id") or ws.headers.get("x-device-id")
        token = ws.query_params.get("token") or ws.headers.get("x-device-token")
        if device_id and token:
            with SessionLocal() as db:
                device = db.scalar(select(Device).where(Device.public_id == device_id))
                if (device and device.enabled and hmac.compare_digest(device.token_hash, sha256(token))):
                    device.last_seen = utcnow()
                    device.last_ip = ws.client.host if ws.client else ""
                    db.commit()
                    return (device.public_id, device.id, config_payload(db, device.settings))
            return None
        if settings.allow_anonymous_devices:
            host = ws.client.host if ws.client else "unknown"
            return (f"anon-{host}", None, DeviceSettingsOut.model_validate(DeviceSettings(
                wake_word="nils", voice_gender="male", character_id="friendly", language="ru",
                volume=70, timezone="Europe/Moscow", home_city="", revision=0)).model_dump())
        return None

    def is_unknown_device(self, ws: WebSocket) -> bool:
        """Плата предъявила идентификатор и токен, но такого устройства нет (удалено): ей нужен сброс, а не вечный отказ."""
        device_id = ws.query_params.get("device_id") or ws.headers.get("x-device-id")
        token = ws.query_params.get("token") or ws.headers.get("x-device-token")
        if not device_id or not token:
            return False
        with SessionLocal() as db:
            return db.scalar(select(Device.id).where(Device.public_id == device_id)) is None

    # ---------- endpoint ----------
    async def handle(self, ws: WebSocket):
        auth = self.authenticate(ws)
        if auth is None:
            if self.is_unknown_device(ws):
                # Удалённое устройство: просим плату сбросить настройки и вернуться в режим сопряжения (повторная настройка по Bluetooth)
                await ws.accept()
                try:
                    await ws.send_json({"type": "command", "name": "factory_reset"})
                    await ws.close(code=4410)
                except Exception:
                    pass
                logger.info("устройство с удалённым идентификатором (%s): отправлен сброс", ws.client.host if ws.client else "?")
                return
            await ws.close(code=4401)
            return
        key, device_pk, dev_settings = auth
        conn_id = str(uuid.uuid4())[:8]
        conn = None
        try:
            await ws.accept()
            conn = DeviceConnection(ws, conn_id, key, device_pk, dev_settings)
            self.active[conn_id] = conn
            if device_pk is not None:
                registry.online_devices[key] = conn
            logger.info("[%s] подключено устройство %s (%s)", conn_id, key, conn.host)
            await ws.send_json({"type": "hello", "message": "Connected to Noctis Voice Server",
                                "client_id": conn_id})
            await conn.push_config(dev_settings)
            if key in self.pending:
                self.spawn(self.deliver_pending(conn))

            while True:
                raw = await ws.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = message.get("type")
                if t == "ping":
                    await ws.send_json({"type": "pong"})
                elif t == "start_recording":
                    conn.start_recording()
                    session = registry.players.get(key)
                    if session is not None and session.state == "playing":
                        self.spawn(session.voice_pause())              # на время команды музыка на паузе
                        self.spawn(self._resume_music_later(conn, session))
                elif t == "audio_chunk":
                    data = message.get("data")
                    if not data or not isinstance(data, str):
                        continue
                    try:
                        conn.add_audio(base64.b64decode(data, validate=True))
                    except Exception as e:
                        logger.error("[%s] чанк отклонён: %s", conn_id, e)
                elif t == "device_info":
                    # плата представилась (после каждого подключения) — отвечаем временем и расписанием
                    self._on_device_info(conn, message)
                    await sch.push_time_sync(conn)
                    await sch.push_schedule(conn)
                elif t == "ota_status":
                    self._on_ota_status(conn, message)
                elif t == "wifi_result":
                    self._on_wifi_result(conn, message)
                elif t == "sched_event":
                    self.spawn(self._on_sched_event(conn, message))
                elif t == "ring_stopped":
                    registry.ringing.pop(key, None)
                elif t == "stop_recording":
                    if not conn.is_recording or (conn.processing_task and not conn.processing_task.done()):
                        continue
                    conn.processing_task = self.spawn(self.process_audio(conn))
                else:
                    logger.warning("[%s] неизвестный тип сообщения: %s", conn_id, t)
        except WebSocketDisconnect:
            logger.info("[%s] устройство отключилось", conn_id)
        except Exception as e:
            logger.exception("[%s] ошибка соединения: %s", conn_id, e)
        finally:
            if conn:
                self.active.pop(conn.id, None)
                if registry.online_devices.get(conn.key) is conn:
                    del registry.online_devices[conn.key]
