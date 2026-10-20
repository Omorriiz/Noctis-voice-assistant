"""Виртуальная колонка — имитация ESP32 для проверки сервера без железа.

Разбор сообщений сервера и сборка ответов выполняет НАСТОЯЩИЙ код прошивки (firmware/components/
noctis_core, скомпилированный в библиотеку и вызываемый через ctypes), поэтому тесты проверяют именно
логику прошивки, а не её копию на Python.

Имитируется (и это не проверяет реальное железо): NVS (JSON-файл), микрофон (готовые PCM-данные),
динамик (накопление принятых чанков), загрузка OTA (HTTP-клиент передаётся снаружи), перезагрузка.
"""
import base64
import ctypes
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

FIRMWARE = Path(__file__).resolve().parents[2] / "firmware"
CORE = FIRMWARE / "components" / "noctis_core"
VENDOR = FIRMWARE / "tests" / "vendor"


class Settings(ctypes.Structure):
    _fields_ = [("wake_word", ctypes.c_char * 64), ("voice_gender", ctypes.c_char * 8),
                ("character_id", ctypes.c_char * 16), ("language", ctypes.c_char * 3),
                ("timezone", ctypes.c_char * 64), ("volume", ctypes.c_int), ("revision", ctypes.c_int)]

    def as_dict(self) -> dict:
        return {k: (getattr(self, k).decode() if isinstance(getattr(self, k), bytes) else getattr(self, k))
                for k, _ in self._fields_}


class Link(ctypes.Structure):
    _fields_ = [("server", ctypes.c_char * 96), ("tls", ctypes.c_bool), ("device_id", ctypes.c_char * 32),
                ("token", ctypes.c_char * 64)]


class Command(ctypes.Structure):
    _fields_ = [("cmd", ctypes.c_int), ("version", ctypes.c_char * 16),
                ("sha256", ctypes.c_char * 65), ("size", ctypes.c_int),
                ("ssid", ctypes.c_char * 33), ("password", ctypes.c_char * 65), ("request_id", ctypes.c_char * 17)]


class WifiSwitch(ctypes.Structure):
    _fields_ = [("trying", ctypes.c_bool), ("started_ms", ctypes.c_int64), ("timeout_ms", ctypes.c_int64),
                ("report_pending", ctypes.c_bool), ("report_ok", ctypes.c_bool), ("request_id", ctypes.c_char * 17)]


class Offline(ctypes.Structure):
    _fields_ = [("offline", ctypes.c_bool), ("fired", ctypes.c_bool), ("since_ms", ctypes.c_int64)]


class Manifest(ctypes.Structure):
    _fields_ = [("wake_word", ctypes.c_char * 64), ("label", ctypes.c_char * 64), ("feature_step_size", ctypes.c_int),
                ("probability_cutoff", ctypes.c_float), ("sliding_window_size", ctypes.c_int),
                ("tensor_arena_size", ctypes.c_int)]


class Container(ctypes.Structure):
    _fields_ = [("manifest", Manifest), ("model", ctypes.c_void_p), ("model_len", ctypes.c_size_t),
                ("total_len", ctypes.c_size_t), ("sha256", ctypes.c_uint8 * 32)]


class SchedTimer(ctypes.Structure):
    _fields_ = [("id", ctypes.c_int32), ("end_epoch", ctypes.c_int64), ("fired", ctypes.c_bool),
                ("label", ctypes.c_char * 24)]


class SchedAlarm(ctypes.Structure):
    _fields_ = [("id", ctypes.c_int32), ("hour", ctypes.c_uint8), ("minute", ctypes.c_uint8),
                ("weekdays", ctypes.c_uint8), ("date_ymd", ctypes.c_int32), ("last_key", ctypes.c_int32),
                ("label", ctypes.c_char * 24), ("melody", ctypes.c_char * 12)]


class SchedState(ctypes.Structure):
    _fields_ = [("rev", ctypes.c_int32), ("n_timers", ctypes.c_int), ("n_alarms", ctypes.c_int),
                ("timers", SchedTimer * 8), ("alarms", SchedAlarm * 16)]


class SchedEvent(ctypes.Structure):
    _fields_ = [("kind", ctypes.c_int), ("id", ctypes.c_int32), ("label", ctypes.c_char * 24),
                ("melody", ctypes.c_char * 12)]


class Ring(ctypes.Structure):
    _fields_ = [("buf", ctypes.c_void_p), ("cap", ctypes.c_size_t), ("head", ctypes.c_size_t), ("size", ctypes.c_size_t)]


class Player(ctypes.Structure):
    _fields_ = [("ring", Ring), ("state", ctypes.c_int), ("ended", ctypes.c_bool), ("paused", ctypes.c_bool),
                ("title", ctypes.c_char * 48), ("artist", ctypes.c_char * 48), ("source", ctypes.c_char * 10),
                ("overruns", ctypes.c_uint32), ("underruns", ctypes.c_uint32), ("received_bytes", ctypes.c_uint32), ("prebuffer", ctypes.c_size_t)]


CMD_NONE, CMD_REBOOT, CMD_FACTORY_RESET, CMD_OTA, CMD_STOP_RING, CMD_SET_WIFI = 0, 1, 2, 3, 4, 5
WIFI_NONE, WIFI_COMMIT, WIFI_ROLLBACK = 0, 1, 2
PLAYER_IDLE, PLAYER_BUFFERING, PLAYER_PLAYING, PLAYER_PAUSED = 0, 1, 2, 3
PLAYER_BUFFER_BYTES = 96 * 1024
ENGINE_ESP_SR, ENGINE_MWW = 0, 1
MSG_OTHER, MSG_CONFIG, MSG_COMMAND = 0, 1, 2
FIRMWARE_VERSION = "0.4.0"


def build_core_library(out_dir: Path) -> Path:
    """Компилирует noctis_core + cJSON в общую библиотеку (нужен gcc/clang или пакет ziglang)."""
    suffix = ".dll" if sys.platform == "win32" else ".so"
    lib = out_dir / f"noctis_core{suffix}"
    sources = [CORE / "noctis_core.c", CORE / "noctis_sched.c", CORE / "noctis_audio.c"]
    headers = list((CORE / "include").glob("*.h"))
    newest = max(p.stat().st_mtime for p in sources + headers)
    if lib.exists() and lib.stat().st_mtime > newest:
        return lib
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("gcc", "clang"):
        if shutil.which(name):
            cc = [name]
            break
    else:
        try:
            import ziglang  # noqa: F401
            cc = [sys.executable, "-m", "ziglang", "cc"]
        except ImportError:
            raise RuntimeError("Нет компилятора Си (gcc/clang/ziglang)")
    subprocess.check_call(cc + ["-shared", "-DNOCTIS_SHARED", "-O1", "-w", "-std=c11", "-I", str(CORE / "include"), "-I", str(VENDOR),
                                *map(str, sources), str(VENDOR / "cJSON.c"), "-o", str(lib)])
    return lib


class Core:
    """Обёртка над библиотекой noctis_core."""

    def __init__(self, lib_path: Path):
        self.lib = ctypes.CDLL(str(lib_path))
        L = self.lib
        L.noctis_settings_defaults.argtypes = [ctypes.POINTER(Settings)]
        L.noctis_settings_sanitize.argtypes = [ctypes.POINTER(Settings)]
        L.noctis_parse_server_message.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(Settings),
                                                  ctypes.POINTER(Command)]
        L.noctis_parse_link.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(Link)]
        L.noctis_parse_link.restype = ctypes.c_bool
        L.noctis_build_device_info.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_uint,
                                               ctypes.c_char_p]
        L.noctis_build_ota_status.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p,
                                              ctypes.c_char_p]
        L.noctis_build_auth_headers.argtypes = [ctypes.POINTER(Link), ctypes.c_char_p, ctypes.c_size_t]
        L.noctis_build_ota_url.argtypes = [ctypes.POINTER(Link), ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        L.noctis_version_compare.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        L.noctis_sha256.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
        L.noctis_wake_engine.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_char_p)]
        L.noctis_wake_engine.restype = ctypes.c_int
        L.noctis_parse_wake_model.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p,
                                              ctypes.POINTER(ctypes.c_int)]
        L.noctis_parse_wake_model.restype = ctypes.c_bool
        L.noctis_wake_model_needed.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        L.noctis_wake_model_needed.restype = ctypes.c_bool
        L.noctis_build_wakeword_url.argtypes = [ctypes.POINTER(Link), ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        L.nww_container_parse.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(Container),
                                          ctypes.POINTER(ctypes.c_char_p)]
        L.nww_container_parse.restype = ctypes.c_bool
        L.nww_container_total_size.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        L.nww_container_total_size.restype = ctypes.c_size_t
        L.noctis_is_valid_wake_word.argtypes = [ctypes.c_char_p]
        L.noctis_is_valid_wake_word.restype = ctypes.c_bool
        # --- смена сети и повторная настройка
        L.noctis_parse_link_update.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(Link), ctypes.c_bool]
        L.noctis_parse_link_update.restype = ctypes.c_bool
        L.noctis_build_wifi_result.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_bool, ctypes.c_char_p]
        L.noctis_wifi_switch_begin.argtypes = [ctypes.POINTER(WifiSwitch), ctypes.c_char_p, ctypes.c_int64, ctypes.c_int64]
        L.noctis_wifi_switch_poll.argtypes = [ctypes.POINTER(WifiSwitch), ctypes.c_int64, ctypes.c_bool]
        L.noctis_wifi_switch_poll.restype = ctypes.c_int
        L.noctis_wifi_switch_take_report.argtypes = [ctypes.POINTER(WifiSwitch), ctypes.c_bool, ctypes.POINTER(ctypes.c_bool),
                                                     ctypes.c_char_p]
        L.noctis_wifi_switch_take_report.restype = ctypes.c_bool
        L.noctis_offline_init.argtypes = [ctypes.POINTER(Offline)]
        L.noctis_offline_poll.argtypes = [ctypes.POINTER(Offline), ctypes.c_int64, ctypes.c_bool, ctypes.c_int64]
        L.noctis_offline_poll.restype = ctypes.c_bool
        # --- этап 5
        L.sched_state_size.restype = ctypes.c_size_t
        L.noctis_player_size.restype = ctypes.c_size_t
        assert L.sched_state_size() == ctypes.sizeof(SchedState), "раскладка sched_state_t не совпала с ctypes"
        assert L.noctis_player_size() == ctypes.sizeof(Player), "раскладка noctis_player_t не совпала с ctypes"
        L.sched_init.argtypes = [ctypes.POINTER(SchedState)]
        L.sched_parse.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(SchedState), ctypes.POINTER(SchedState)]
        L.sched_parse.restype = ctypes.c_bool
        L.sched_poll.argtypes = [ctypes.POINTER(SchedState), ctypes.c_int64, ctypes.c_int32, ctypes.POINTER(SchedEvent),
                                 ctypes.c_int]
        L.sched_summary.argtypes = [ctypes.POINTER(SchedState), ctypes.c_int64, ctypes.c_int32,
                                    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        L.sched_serialize.argtypes = [ctypes.POINTER(SchedState), ctypes.c_char_p, ctypes.c_size_t]
        L.sched_serialize.restype = ctypes.c_size_t
        L.sched_deserialize.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(SchedState)]
        L.sched_deserialize.restype = ctypes.c_bool
        L.sched_build_event.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int32]
        L.sched_build_ring_stopped.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        L.noctis_parse_time_sync.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_int64),
                                             ctypes.POINTER(ctypes.c_int32)]
        L.noctis_parse_time_sync.restype = ctypes.c_bool
        L.noctis_format_clock.argtypes = [ctypes.c_int64, ctypes.c_int32, ctypes.c_char_p, ctypes.c_size_t]
        L.noctis_time_valid.argtypes = [ctypes.c_int64]
        L.noctis_time_valid.restype = ctypes.c_bool
        L.noctis_player_init.argtypes = [ctypes.POINTER(Player), ctypes.c_void_p, ctypes.c_size_t]
        L.noctis_player_message.argtypes = [ctypes.POINTER(Player), ctypes.c_char_p, ctypes.c_size_t]
        L.noctis_player_pull.argtypes = [ctypes.POINTER(Player), ctypes.c_char_p, ctypes.c_size_t]
        L.noctis_player_pull.restype = ctypes.c_size_t
        L.noctis_player_stop.argtypes = [ctypes.POINTER(Player)]

    def defaults(self) -> Settings:
        s = Settings()
        self.lib.noctis_settings_defaults(ctypes.byref(s))
        return s

    def parse_message(self, message: dict, settings: Settings):
        raw = json.dumps(message).encode()
        cmd = Command()
        kind = self.lib.noctis_parse_server_message(raw, len(raw), ctypes.byref(settings), ctypes.byref(cmd))
        return kind, cmd

    def parse_link(self, payload: dict) -> Link | None:
        raw = json.dumps(payload).encode()
        link = Link()
        return link if self.lib.noctis_parse_link(raw, len(raw), ctypes.byref(link)) else None

    def parse_link_update(self, payload: dict, existing: Link | None) -> Link | None:
        raw = json.dumps(payload).encode()
        link = existing if existing is not None else Link()
        ok = self.lib.noctis_parse_link_update(raw, len(raw), ctypes.byref(link), existing is not None)
        return link if ok else None

    def wifi_result(self, request_id: str, ok: bool, error: str | None = None) -> dict:
        return json.loads(self._build(self.lib.noctis_build_wifi_result, request_id.encode(), ok,
                                      error.encode() if error else None))

    def _build(self, fn, *args) -> str:
        buf = ctypes.create_string_buffer(400)
        n = fn(buf, len(buf), *args)
        assert n > 0, "builder failed"
        return buf.value.decode()

    def device_info(self, revision: int, free_heap: int, reason: str) -> dict:
        return json.loads(self._build(self.lib.noctis_build_device_info, revision, free_heap, reason.encode()))

    def ota_status(self, version: str, state: str, error: str | None = None) -> dict:
        return json.loads(self._build(self.lib.noctis_build_ota_status, version.encode(), state.encode(),
                                      error.encode() if error else None))

    def auth_headers(self, link: Link) -> dict:
        buf = ctypes.create_string_buffer(400)
        assert self.lib.noctis_build_auth_headers(ctypes.byref(link), buf, len(buf)) > 0
        text = buf.value.decode()
        return dict(line.split(": ", 1) for line in text.strip().split("\r\n"))

    def ota_url(self, link: Link, version: str) -> str | None:
        buf = ctypes.create_string_buffer(256)
        n = self.lib.noctis_build_ota_url(ctypes.byref(link), version.encode(), buf, len(buf))
        return buf.value.decode() if n > 0 else None

    def sha256(self, data: bytes) -> bytes:
        out = ctypes.create_string_buffer(32)
        self.lib.noctis_sha256(data, len(data), out)
        return out.raw

    def engine(self, wake_word: str) -> tuple[int, str | None]:
        model = ctypes.c_char_p()
        eng = self.lib.noctis_wake_engine(wake_word.encode(), ctypes.byref(model))
        return eng, (model.value.decode() if model.value else None)

    def parse_wake_model(self, message: dict) -> tuple[str, int] | None:
        raw = json.dumps(message).encode()
        sha, size = ctypes.create_string_buffer(65), ctypes.c_int(0)
        return (sha.value.decode(), size.value) if self.lib.noctis_parse_wake_model(raw, len(raw), sha, ctypes.byref(size)) else None

    def wake_model_needed(self, wake_word: str, installed: str, server: str | None) -> bool:
        return self.lib.noctis_wake_model_needed(wake_word.encode(), installed.encode(), server.encode() if server else None)

    def wakeword_url(self, link: Link, sha: str) -> str | None:
        buf = ctypes.create_string_buffer(256)
        n = self.lib.noctis_build_wakeword_url(ctypes.byref(link), sha.encode(), buf, len(buf))
        return buf.value.decode() if n > 0 else None

    def parse_container(self, blob: bytes, avail: int | None = None):
        """(dict манифеста, None) или (None, текст ошибки) — результат настоящего nww_container_parse()."""
        c, err = Container(), ctypes.c_char_p()
        ok = self.lib.nww_container_parse(blob, len(blob) if avail is None else avail, ctypes.byref(c), ctypes.byref(err))
        if not ok:
            return None, err.value.decode()
        m = c.manifest
        return {"wake_word": m.wake_word.decode(), "label": m.label.decode(), "cutoff": m.probability_cutoff,
                "window": m.sliding_window_size, "arena": m.tensor_arena_size, "total": c.total_len,
                "model_len": c.model_len}, None

    def container_total(self, blob: bytes) -> int:
        return self.lib.nww_container_total_size(blob, len(blob))

    def valid_wake_word(self, value: str) -> bool:
        return self.lib.noctis_is_valid_wake_word(value.encode())

    def newer(self, a: str, b: str) -> bool:
        return self.lib.noctis_version_compare(a.encode(), b.encode()) > 0

    # ---------- этап 5: расписание, время, плеер (настоящий код прошивки) ----------
    def sched_apply(self, message: dict, prev: SchedState) -> SchedState | None:
        raw = json.dumps(message).encode()
        out = SchedState()
        ok = self.lib.sched_parse(raw, len(raw), ctypes.byref(prev), ctypes.byref(out))
        return out if ok else None

    def sched_poll(self, state: SchedState, now: int, utc_offset: int, limit: int = 4) -> list[dict]:
        events = (SchedEvent * limit)()
        n = self.lib.sched_poll(ctypes.byref(state), now, utc_offset, events, limit)
        return [{"kind": "timer" if e.kind == 1 else "alarm", "id": e.id, "label": e.label.decode(),
                 "melody": e.melody.decode()} for e in events[:n]]

    def sched_summary(self, state: SchedState, now: int, utc_offset: int) -> dict:
        left, h, m = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        self.lib.sched_summary(ctypes.byref(state), now, utc_offset, ctypes.byref(left), ctypes.byref(h), ctypes.byref(m))
        return {"timer_left": left.value, "alarm": None if h.value < 0 else (h.value, m.value)}

    def sched_blob(self, state: SchedState) -> bytes:
        buf = ctypes.create_string_buffer(1200)
        n = self.lib.sched_serialize(ctypes.byref(state), buf, len(buf))
        assert n > 0
        return buf.raw[:n]

    def sched_load(self, blob: bytes) -> SchedState | None:
        out = SchedState()
        return out if self.lib.sched_deserialize(blob, len(blob), ctypes.byref(out)) else None

    def sched_event_json(self, kind: str, item_id: int) -> dict:
        return json.loads(self._build(self.lib.sched_build_event, 1 if kind == "timer" else 2, item_id))

    def parse_time_sync(self, message: dict) -> tuple[int, int] | None:
        raw = json.dumps(message).encode()
        epoch, off = ctypes.c_int64(), ctypes.c_int32()
        ok = self.lib.noctis_parse_time_sync(raw, len(raw), ctypes.byref(epoch), ctypes.byref(off))
        return (epoch.value, off.value) if ok else None

    def clock_text(self, epoch: int, utc_offset: int) -> str:
        buf = ctypes.create_string_buffer(8)
        assert self.lib.noctis_format_clock(epoch, utc_offset, buf, len(buf)) > 0
        return buf.value.decode()

    def time_valid(self, epoch: int) -> bool:
        return self.lib.noctis_time_valid(epoch)


class VirtualSpeaker:
    """Одно виртуальное устройство. `nvs_path` — файл, имитирующий NVS (переживает «перезагрузку»)."""

    def __init__(self, core: Core, nvs_path: Path):
        self.core = core
        self.nvs_path = nvs_path
        self.boot_count = 0
        self.active_slot = "ota_0"
        self.firmware = FIRMWARE_VERSION
        self.last_command: int = CMD_NONE
        self.played_bytes = 0
        self.volume_applied: int | None = None
        self.ota_log: list[tuple[str, str]] = []
        self.wake_partition = b"\xff" * 4096     # раздел wakeword (стёртый flash)
        self.wake_log: list[tuple[str, str]] = []
        self.restarts = 0
        # этап 5: часы (RTC нет — после включения время неизвестно), звонок, музыка
        self.epoch: int | None = None
        self.sntp_synced = False
        self.ringing = False
        self.ring_melody = ""
        self.ring_log: list[dict] = []
        self.sent: list[dict] = []
        self.audio_idle = True              # ST_LISTENING: динамик свободен для музыки
        self.music_pcm = bytearray()
        self.boot()

    # ---------- «NVS» ----------
    def boot(self):
        """Загрузка: читает NVS так же, как settings_init()."""
        self.boot_count += 1
        data = json.loads(self.nvs_path.read_text()) if self.nvs_path.exists() else {}
        self.settings = self.core.defaults()
        for key, value in data.get("settings", {}).items():
            setattr(self.settings, key, value.encode() if isinstance(value, str) else value)
        self.core.lib.noctis_settings_sanitize(ctypes.byref(self.settings))
        self.link_data = data.get("link")
        self.ssid = data.get("ssid", "")                 # сохранённая сеть (как в NVS радиомодуля)
        self.wifi_password = data.get("wifi_password", "")
        self.reconfigure_mode = bool(data.get("reconfigure", False))  # следующая загрузка — окно Bluetooth-настройки
        self.switch = WifiSwitch()
        self.clock_ms = 0
        self._old_network: tuple[str, str] | None = None
        self.volume_applied = self.settings.volume
        # движок и установленная модель определяются при загрузке (как afe_init() в main.c)
        self.engine, self.wn_model = self.core.engine(self.settings.wake_word.decode())
        total = self.core.container_total(self.wake_partition)
        parsed, _err = self.core.parse_container(self.wake_partition[:total]) if total else (None, None)
        self.installed_sha = self.core.sha256(self.wake_partition[:total]).hex() if parsed else ""
        self.engine_running = self.engine
        if self.engine == ENGINE_MWW and not parsed:
            self.engine_running, self.wn_model = ENGINE_ESP_SR, "privetesp"  # модели нет — временно «Привет ESP»
        # этап 5: после перезагрузки время неизвестно (до NTP/сервера), расписание — из NVS (если цело)
        self.epoch = None
        self.sntp_synced = False
        self.ringing = False
        self.utc_offset = int(data.get("utcoff", 0))
        self.sched = SchedState()
        self.core.lib.sched_init(ctypes.byref(self.sched))
        self.sched_loaded = False
        blob = bytes.fromhex(data["sched_hex"]) if data.get("sched_hex") else None
        if blob:
            loaded = self.core.sched_load(blob)
            if loaded is not None:
                self.sched, self.sched_loaded = loaded, True
        self.player_store = (ctypes.c_uint8 * PLAYER_BUFFER_BYTES)()
        self.player = Player()
        self.core.lib.noctis_player_init(ctypes.byref(self.player), ctypes.cast(self.player_store, ctypes.c_void_p),
                                         PLAYER_BUFFER_BYTES)

    def _save(self):
        old = json.loads(self.nvs_path.read_text()) if self.nvs_path.exists() else {}
        old.update({"settings": self.settings.as_dict(), "link": self.link_data})
        self.nvs_path.write_text(json.dumps(old))

    def _save_extra(self, **fields):
        data = json.loads(self.nvs_path.read_text()) if self.nvs_path.exists() else {}
        data.update(fields)
        self.nvs_path.write_text(json.dumps(data))

    def _save_sched(self):
        self._save_extra(sched_hex=self.core.sched_blob(self.sched).hex())

    def pair(self, payload: dict) -> bool:
        """Приём данных от приложения по BLE (endpoint noctis-config)."""
        link = self.core.parse_link(payload)
        if link is None:
            return False
        self.link_data = {"server": link.server.decode(), "tls": link.tls, "device_id": link.device_id.decode(),
                          "token": link.token.decode()}
        self._save()
        return True

    # ---------- повторная настройка (Bluetooth) и смена сети ----------
    def pair_update(self, payload: dict) -> bool:
        """Endpoint noctis-config в режиме повторной настройки: новый адрес сервера, личность платы сохраняется."""
        existing = self.link if self.link_data else None
        link = self.core.parse_link_update(payload, existing)
        if link is None:
            return False
        self.link_data = {"server": link.server.decode(), "tls": link.tls, "device_id": link.device_id.decode(),
                          "token": link.token.decode()}
        self._save()
        return True

    def provision_wifi(self, ssid: str, password: str):
        """Wi-Fi, присланный приложением по Bluetooth."""
        self.ssid, self.wifi_password = ssid, password
        self._save_extra(ssid=ssid, wifi_password=password)

    def enter_reconfigure(self):
        """Долгое нажатие BOOT / долгая потеря сервера: флаг в NVS и перезагрузка в окно Bluetooth-настройки."""
        self._save_extra(reconfigure=True)
        self.boot()

    def finish_reconfigure(self):
        self._save_extra(reconfigure=False)
        self.boot()

    # Мир для проверок: какие сети есть и доступен ли в них сервер. {ssid: {"password": str, "server_ok": bool}}
    networks: dict = {}

    def server_ok(self) -> bool:
        net = self.networks.get(self.ssid)
        return bool(net and net["password"] == self.wifi_password and net.get("server_ok", True))

    def _start_wifi_switch(self, cmd) -> bool:
        if self.switch.trying:
            return False
        self._old_network = (self.ssid, self.wifi_password)
        self.ssid, self.wifi_password = cmd.ssid.decode(), cmd.password.decode()
        self._save_extra(ssid=self.ssid, wifi_password=self.wifi_password)   # esp_wifi_set_config пишет в NVS сразу
        self.core.lib.noctis_wifi_switch_begin(ctypes.byref(self.switch), cmd.request_id, self.clock_ms, 45000)
        return True

    def net_tick(self, ws, ms: int = 1000) -> int:
        """Проходит `ms` миллисекунд: решение по смене сети (подтвердить/откатить) и отправка итога серверу."""
        self.clock_ms += ms
        lib = self.core.lib
        decision = lib.noctis_wifi_switch_poll(ctypes.byref(self.switch), self.clock_ms, self.server_ok())
        if decision == WIFI_ROLLBACK and self._old_network:
            self.ssid, self.wifi_password = self._old_network
            self._save_extra(ssid=self.ssid, wifi_password=self.wifi_password)
        if decision != WIFI_NONE:
            self._old_network = None
        ok, rid = ctypes.c_bool(), ctypes.create_string_buffer(17)
        if lib.noctis_wifi_switch_take_report(ctypes.byref(self.switch), self.server_ok(), ctypes.byref(ok), rid):
            msg = self.core.wifi_result(rid.value.decode(), ok.value, None if ok.value else "connect_failed")
            self.sent.append(msg)
            ws.send_json(msg)
        return decision

    @property
    def link(self) -> Link:
        link = Link()
        link.server = self.link_data["server"].encode()
        link.tls = self.link_data["tls"]
        link.device_id = self.link_data["device_id"].encode()
        link.token = self.link_data["token"].encode()
        return link

    def headers(self) -> dict:
        return self.core.auth_headers(self.link)

    def factory_reset(self):
        self.nvs_path.unlink(missing_ok=True)
        self.link_data = None

    # ---------- сообщения сервера ----------
    def handle(self, ws, message: dict, http_get=None):
        """Эквивалент handle_server_message()/control_handle() прошивки."""
        t = message.get("type")
        if t in ("config", "command"):
            old_word = self.settings.wake_word
            kind, cmd = self.core.parse_message(message, self.settings)
            if kind == MSG_CONFIG:
                self.volume_applied = self.settings.volume
                self._save()
                self.config_applied = True
                self._wake_model_flow(ws, message, old_word != self.settings.wake_word, http_get)
            elif kind == MSG_COMMAND:
                self.last_command = cmd.cmd
                if cmd.cmd == CMD_STOP_RING:
                    self.stop_ring(ws, notify=False)
                elif cmd.cmd == CMD_REBOOT:
                    self.boot()
                elif cmd.cmd == CMD_FACTORY_RESET:
                    self.factory_reset()
                    self.boot()
                elif cmd.cmd == CMD_OTA:
                    self._ota(ws, cmd, http_get)
                elif cmd.cmd == CMD_SET_WIFI:
                    if not self._start_wifi_switch(cmd):
                        msg = self.core.wifi_result(cmd.request_id.decode(), False, "busy")
                        self.sent.append(msg)
                        ws.send_json(msg)
        elif t == "audio_response_chunk":
            self.played_bytes += len(base64.b64decode(message["data"]))
        elif t == "audio_response_start":
            self.stop_ring(ws, notify=False)                       # голос замолкает звонок
        elif t == "time_sync":
            parsed = self.core.parse_time_sync(message)
            if parsed:
                epoch, off = parsed
                if off != self.utc_offset:
                    self.utc_offset = off
                    self._save_extra(utcoff=off)
                if not self.sntp_synced:                           # NTP точнее сервера
                    self.epoch = epoch
        elif t == "schedule":
            new = self.core.sched_apply(message, self.sched)
            if new is not None:
                self.sched = new
                self._save_sched()
        elif t.startswith("music_"):
            raw = json.dumps(message).encode()
            self.core.lib.noctis_player_message(ctypes.byref(self.player), raw, len(raw))

    def _wake_model_flow(self, ws, message: dict, word_changed: bool, http_get):
        """Повторяет логику control.c + wakeword_sync.c: скачать модель, проверить ДО записи, перезапуститься."""
        word = self.settings.wake_word.decode()
        wm = self.core.parse_wake_model(message)
        if wm and self.core.wake_model_needed(word, self.installed_sha, wm[0]):
            sha, size = wm
            url = self.core.wakeword_url(self.link, sha)
            r = http_get(url, self.headers()) if http_get else None
            if r is None or r.status_code != 200:
                self.wake_log.append((sha, "http"))
            elif len(r.content) != size or hashlib.sha256(r.content).hexdigest() != sha:
                self.wake_log.append((sha, "sha256 mismatch"))          # во flash не пишем
            else:
                parsed, err = self.core.parse_container(r.content)
                if parsed is None:
                    self.wake_log.append((sha, f"bad container: {err}"))
                else:
                    self.wake_partition = r.content + b"\xff" * 16     # запись + «мусор» после контейнера
                    self.wake_log.append((sha, "ok"))
                    self.restarts += 1
                    self.boot()
                    return
        if word_changed:
            self.restarts += 1                                           # смена слова → перезапуск в новый движок
            self.boot()

    def send_device_info(self, ws):
        ws.send_json(self.core.device_info(self.settings.revision, 200000, "poweron"))

    # ---------- этап 5: часы, расписание, звонок (повторяет schedule.c / clock.c) ----------
    def set_clock(self, epoch: int, sntp: bool = True):
        """Плата получила время по NTP (в тестах — вручную)."""
        self.epoch, self.sntp_synced = epoch, sntp

    def advance(self, seconds: int):
        if self.epoch is not None:
            self.epoch += seconds

    def clock_text(self) -> str:
        return self.core.clock_text(self.epoch, self.utc_offset) if self.epoch is not None else "--:--"

    def summary(self) -> dict:
        return self.core.sched_summary(self.sched, self.epoch or 0, self.utc_offset)

    def tick(self, ws=None) -> list[dict]:
        """Один проход задачи опроса: сработавшее сохраняется в NVS, уходит серверу (если есть связь) и включает звонок."""
        if self.epoch is None:
            return []
        events = self.core.sched_poll(self.sched, self.epoch, self.utc_offset)
        if events:
            self._save_sched()
        for ev in events:
            self.ring_log.append(ev)
            if ws is not None:
                msg = self.core.sched_event_json(ev["kind"], ev["id"])
                ws.send_json(msg)
                self.sent.append(msg)
            self.ringing, self.ring_melody = True, ev["melody"]
            self.music_stop_local()
        return events

    def stop_ring(self, ws=None, notify=True):
        was, self.ringing = self.ringing, False
        if was and notify and ws is not None:
            ws.send_json({"type": "ring_stopped"})

    # ---------- этап 5: музыка (повторяет music.c) ----------
    @property
    def music_state(self) -> int:
        return self.player.state

    def music_stop_local(self):
        self.core.lib.noctis_player_stop(ctypes.byref(self.player))

    def pump_music(self, max_bytes: int = 4096) -> int:
        """Задача воспроизведения: забирает данные, только когда динамик свободен."""
        if not self.audio_idle:
            return 0
        buf = ctypes.create_string_buffer(max_bytes)
        n = self.core.lib.noctis_player_pull(ctypes.byref(self.player), buf, max_bytes)
        self.music_pcm += buf.raw[:n]
        return n

    # ---------- OTA (повторяет ota.c) ----------
    def _ota(self, ws, cmd: Command, http_get):
        version = cmd.version.decode()
        ws.send_json(self.core.ota_status(version, "started"))

        def fail(reason):
            self.ota_log.append((version, reason))
            ws.send_json(self.core.ota_status(version, "failed", reason))

        if not self.core.newer(version, self.firmware):
            return fail("not newer")
        url = self.core.ota_url(self.link, version)
        if url is None:
            return fail("bad url")
        r = http_get(url, self.headers())  # адрес строит прошивка из своей ссылки, а не из команды
        if r.status_code != 200:
            return fail(f"http {r.status_code}")
        image = r.content
        if cmd.size and len(image) != cmd.size:
            return fail("bad image size")
        if hashlib.sha256(image).hexdigest() != cmd.sha256.decode().lower():
            return fail("sha256 mismatch")  # образ не записывается, слот не переключается
        self.active_slot = "ota_1" if self.active_slot == "ota_0" else "ota_0"
        self.firmware = version
        self.ota_log.append((version, "ok"))
        ws.send_json(self.core.ota_status(version, "ok"))
        self.boot()

    # ---------- голосовой цикл ----------
    def speak_command(self, ws, pcm: bytes, chunk: int = 3200):
        ws.send_json({"type": "start_recording"})
        for i in range(0, len(pcm), chunk):
            ws.send_json({"type": "audio_chunk", "index": i // chunk,
                          "data": base64.b64encode(pcm[i:i + chunk]).decode()})
        ws.send_json({"type": "stop_recording"})

    def listen_for_reply(self, ws, http_get=None) -> dict:
        """Читает сообщения до конца ответа; применяет их как прошивка. Возвращает тексты и число чанков."""
        texts, chunks, ended = [], 0, False
        while True:
            m = ws.receive_json()
            self.handle(ws, m, http_get)
            if m["type"] == "audio_response_chunk":
                chunks += 1
            elif m["type"] == "audio_response_end":
                ended = True
            elif m["type"] == "text_response":
                texts.append(m["text"])
                if ended and not m["text"].startswith("Вы сказали"):
                    return {"texts": texts, "chunks": chunks}
