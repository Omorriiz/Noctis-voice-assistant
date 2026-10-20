"""Декодирование музыки в PCM16 16 кГц моно (формат плеера платы). Библиотека miniaudio: MP3, FLAC, Vorbis, WAV,
работает на Windows без ffmpeg. AAC-потоки (часть радиостанций) не поддерживаются — для них нужен MP3-поток станции.

`open_pcm(uri)` — блокирующий генератор кусков PCM; вызывающий запускает его в потоке.
Сетевые потоки читает собственный HttpSource (не IceCastClient из miniaudio: тот при обрыве/конце потока
крутится в бесконечном цикле и грузит процессор). В тестах `OPEN` подменяется, чтобы не ходить в сеть.
"""
import logging
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

log = logging.getLogger("noctis.decoder")

SAMPLE_RATE = 16000
FRAMES_PER_READ = 1600          # 100 мс
CONNECT_TIMEOUT_SEC = 10.0
STALL_TIMEOUT_SEC = 15.0        # столько ждём данных от потока, потом считаем его оборвавшимся
BUFFER_BYTES = 96 * 1024
USER_AGENT = "Noctis/0.4"


class DecoderError(RuntimeError):
    pass


def _format_for(content_type: str):
    import miniaudio
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ("audio/mpeg", "audio/mp3"):
        return miniaudio.FileFormat.MP3
    if ct == "audio/flac":
        return miniaudio.FileFormat.FLAC
    if ct in ("audio/ogg", "application/ogg", "audio/vorbis"):
        return miniaudio.FileFormat.VORBIS
    if ct in ("audio/aac", "audio/aacp", "audio/x-aac", "audio/mp4", "application/vnd.apple.mpegurl"):
        raise DecoderError(f"unsupported stream format {ct} (AAC/HLS); use the MP3 stream of the station")
    return miniaudio.FileFormat.UNKNOWN          # WAV и всё прочее miniaudio определит по заголовку


def make_http_source(url: str):
    """Источник miniaudio поверх HTTP(S) с фоновым буфером, разбором ICY-метаданных и корректным концом потока."""
    import miniaudio

    class HttpSource(miniaudio.StreamableSource):
        def __init__(self):
            req = urllib.request.Request(url, headers={"Icy-MetaData": "1", "User-Agent": USER_AGENT})
            try:
                self._resp = urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT_SEC)
            except Exception as e:
                raise DecoderError(f"cannot open stream: {e}") from e
            self.format = _format_for(self._resp.headers.get("Content-Type", ""))
            self._metaint = int(self._resp.headers.get("icy-metaint") or 0)
            self._buf = bytearray()
            self._cv = threading.Condition()
            self._eof = False
            self._stop = False
            self._thread = threading.Thread(target=self._download, daemon=True)
            self._thread.start()

        def _read_exact(self, n: int) -> bytes:
            out = b""
            while len(out) < n:
                part = self._resp.read(n - len(out))
                if not part:
                    return out
                out += part
            return out

        def _download(self):
            until_meta = self._metaint
            try:
                while not self._stop:
                    with self._cv:
                        while len(self._buf) > BUFFER_BYTES and not self._stop:
                            self._cv.wait(0.2)
                    want = 8192 if not self._metaint else min(8192, until_meta)
                    data = self._resp.read(want)
                    if not data:
                        break
                    if self._metaint:
                        until_meta -= len(data)
                        if until_meta == 0:                       # дальше идёт блок метаданных: длина в 16-байтовых блоках
                            size = self._read_exact(1)
                            if size:
                                self._read_exact(size[0] * 16)
                            until_meta = self._metaint
                    with self._cv:
                        self._buf += data
                        self._cv.notify_all()
            except Exception as e:                              # обрыв, таймаут сокета
                log.info("поток %s прервался: %s", url, e)
            finally:
                with self._cv:
                    self._eof = True
                    self._cv.notify_all()

        def read(self, num_bytes: int) -> bytes:
            deadline = time.monotonic() + STALL_TIMEOUT_SEC
            with self._cv:
                while len(self._buf) < num_bytes and not self._eof and not self._stop:
                    if time.monotonic() > deadline:
                        self._eof = True                         # поток завис — считаем оборванным
                        break
                    self._cv.wait(0.2)
                chunk = bytes(self._buf[:num_bytes])
                del self._buf[:num_bytes]
                self._cv.notify_all()
                return chunk                                     # короче запрошенного (или b"") только в конце потока

        def close(self):
            self._stop = True
            try:
                self._resp.close()
            except Exception:
                pass
            with self._cv:
                self._cv.notify_all()
            self._thread.join(2)

    return HttpSource()


def open_pcm(uri: str) -> Iterator[bytes]:
    try:
        import miniaudio
    except ImportError as e:
        raise DecoderError("miniaudio is not installed (pip install miniaudio)") from e
    kwargs = dict(output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=SAMPLE_RATE,
                  frames_to_read=FRAMES_PER_READ)
    source = None
    try:
        if uri.startswith(("http://", "https://")):
            source = make_http_source(uri)
            stream = miniaudio.stream_any(source, source_format=source.format, **kwargs)
        else:
            if not Path(uri).is_file():
                raise DecoderError(f"file not found: {uri}")
            stream = miniaudio.stream_file(uri, **kwargs)
        for samples in stream:
            yield samples.tobytes()
    except DecoderError:
        raise
    except Exception as e:                       # miniaudio.DecodeError и прочее
        raise DecoderError(str(e)) from e
    finally:
        if source is not None:
            source.close()


OPEN = open_pcm
