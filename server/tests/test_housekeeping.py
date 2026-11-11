"""Обслуживание: автоматические копии базы (хранить последние 7), удаление старых журналов, таймаут запросов к Ollama, версия прошивки."""
import re
import sqlite3
import sys
import types
from datetime import date
from pathlib import Path

from noctis_server import backup
from noctis_server.config import settings
from noctis_server.main import prune_logs


def make_db(path: Path):
    with sqlite3.connect(path) as con:
        con.execute("create table t (x integer)")
        con.execute("insert into t values (42)")
    return path


def test_auto_backup_creates_one_copy_per_day(tmp_path):
    db = make_db(tmp_path / "noctis.db")
    url, dest = f"sqlite:///{db.as_posix()}", tmp_path / "backups"
    first = backup.auto_backup(url, dest, keep=7, today=date(2026, 11, 1))
    assert first == dest / "noctis-auto-20261101.db"
    assert sqlite3.connect(first).execute("select x from t").fetchone() == (42,)       # копия рабочая и с данными
    assert backup.auto_backup(url, dest, keep=7, today=date(2026, 11, 1)) is None       # в тот же день повторно не копирует
    assert [p.name for p in dest.glob("*")] == ["noctis-auto-20261101.db"]               # и не оставляет временных файлов


def test_auto_backup_keeps_only_the_last_n_and_leaves_manual_copies(tmp_path):
    db = make_db(tmp_path / "noctis.db")
    url, dest = f"sqlite:///{db.as_posix()}", tmp_path / "backups"
    dest.mkdir()
    for name in ("noctis-before-0005-20261019-2134.db", "noctis-20261018-224500.db", "readme.txt"):
        (dest / name).write_bytes(b"manual")
    for day in range(1, 11):                                   # десять суток подряд
        backup.auto_backup(url, dest, keep=7, today=date(2026, 11, day))
    auto = sorted(p.name for p in dest.glob("noctis-auto-*.db"))
    assert auto == [f"noctis-auto-202611{d:02d}.db" for d in range(4, 11)]               # остались последние семь
    assert (dest / "noctis-before-0005-20261019-2134.db").exists() and (dest / "noctis-20261018-224500.db").exists()
    assert (dest / "readme.txt").exists()


def test_auto_backup_never_breaks_startup(tmp_path):
    assert backup.auto_backup("sqlite:///:memory:", tmp_path / "b", keep=7) is None                     # базы в памяти нет файла
    assert backup.auto_backup(f"sqlite:///{(tmp_path / 'missing.db').as_posix()}", tmp_path / "b", keep=7) is None   # первый запуск: файла ещё нет
    assert backup.auto_backup("postgresql://x/y", tmp_path / "b", keep=7) is None
    db = make_db(tmp_path / "noctis.db")
    assert backup.auto_backup(f"sqlite:///{db.as_posix()}", tmp_path / "b", keep=0) is None              # 0 — копии выключены
    blocker = tmp_path / "blocked"
    blocker.write_text("file, not a folder")
    assert backup.auto_backup(f"sqlite:///{db.as_posix()}", blocker / "sub", keep=7) is None             # папку не создать — ошибка в журнал, не исключение


def test_backup_defaults():
    assert settings.backup_keep == 7 and settings.backup_on_start is True and settings.log_keep_days == 14


def test_old_logs_are_removed_by_date_in_the_name(tmp_path):
    for name in ("server_20261001.log", "server_20261020.log", "server_20261031.log", "server_20261110.log", "other.log", "server_notes.log"):
        (tmp_path / name).write_text("x")
    removed = prune_logs(tmp_path, keep_days=14, today=date(2026, 11, 11))
    assert sorted(removed) == ["server_20261001.log", "server_20261020.log"]
    assert sorted(p.name for p in tmp_path.glob("*")) == ["other.log", "server_20261031.log", "server_20261110.log", "server_notes.log"]
    assert prune_logs(tmp_path, keep_days=0, today=date(2030, 1, 1)) == []            # 0 — ничего не удалять
    assert prune_logs(tmp_path / "nope", keep_days=14) == []


def test_ollama_requests_have_a_timeout(monkeypatch):
    from noctis_server.voice import pipeline
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

        def chat(self, **kw):
            return "ok"

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr(pipeline, "_ollama_client", None)
    assert pipeline.chat(model="m", messages=[]) == "ok"
    t = seen["timeout"]
    assert t.connect == pipeline.OLLAMA_CONNECT_TIMEOUT_SEC and t.read == pipeline.OLLAMA_READ_TIMEOUT_SEC
    assert 0 < t.connect <= 10 and 30 <= t.read <= 300
    pipeline.chat(model="m", messages=[])
    assert pipeline._ollama_client is not None                       # клиент создаётся один раз


def test_firmware_version_is_new_and_documented():
    root = Path(__file__).resolve().parents[2]
    header = (root / "firmware/components/noctis_core/include/noctis_core.h").read_text(encoding="utf-8")
    version = re.search(r'#define NOCTIS_FIRMWARE_VERSION "(\d+\.\d+\.\d+)"', header).group(1)
    assert tuple(map(int, version.split("."))) >= (0, 5, 0)          # после кнопки GPIO11, буфера воспроизведения и сетевых правок
    assert version in (root / "docs/hardware-testing-guide.md").read_text(encoding="utf-8")
