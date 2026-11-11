"""Резервная копия SQLite: python -m noctis_server.backup  →  data/backups/noctis-<время>.db
Использует встроенный backup API SQLite, поэтому безопасна при работающем сервере."""
import logging
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from .config import BASE_DIR, settings

log = logging.getLogger("noctis.backup")
AUTO_RE = re.compile(r"^noctis-auto-(\d{8})\.db$")


def backup() -> str:
    url = settings.database_url
    if not url.startswith("sqlite:///") or ":memory:" in url:
        raise SystemExit("Резервная копия поддерживается только для файла SQLite")
    src_path = url.removeprefix("sqlite:///")
    dest_dir = BASE_DIR / "data" / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"noctis-{datetime.now():%Y%m%d-%H%M%S}.db"
    with closing(sqlite3.connect(src_path)) as src, closing(sqlite3.connect(dest)) as dst:
        src.backup(dst)
    return str(dest)


def _sqlite_file(url: str) -> Path | None:
    if not url.startswith("sqlite:///") or ":memory:" in url:
        return None
    path = Path(url.removeprefix("sqlite:///"))
    return path if path.exists() else None


def auto_backup(url: str | None = None, dest_dir: Path | None = None, keep: int | None = None, today: date | None = None) -> Path | None:
    """Раз в сутки (при запуске сервера) копирует базу в noctis-auto-ГГГГММДД.db и оставляет `keep` последних копий.
    Ручные копии (noctis-<время>.db, noctis-before-…) не трогает. Ошибка копирования не мешает запуску: пишется в журнал."""
    keep = settings.backup_keep if keep is None else keep
    src = _sqlite_file(url or settings.database_url)
    dest_dir = dest_dir or BASE_DIR / "data" / "backups"
    if src is None or keep <= 0:
        return None
    dest = dest_dir / f"noctis-auto-{(today or date.today()):%Y%m%d}.db"
    created = None
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        if not dest.exists():                          # сегодня копия уже есть — повторный запуск её не плодит
            tmp = dest.with_suffix(".tmp")
            with closing(sqlite3.connect(src)) as s, closing(sqlite3.connect(tmp)) as d:    # closing: на Windows открытый файл не переименовать
                s.backup(d)
            tmp.replace(dest)
            created = dest
            log.info("Резервная копия базы: %s", dest.name)
    except Exception as e:
        log.warning("Не удалось сделать резервную копию базы: %s", e)
    prune_auto_backups(dest_dir, keep)
    return created


def prune_auto_backups(dest_dir: Path, keep: int) -> list[str]:
    """Удаляет автоматические копии, кроме `keep` самых свежих (по дате в имени)."""
    found = sorted((m.group(1), p) for p in dest_dir.glob("noctis-auto-*.db") if (m := AUTO_RE.match(p.name)))
    removed = []
    for _, path in found[:max(0, len(found) - keep)]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError as e:
            log.warning("Не удалось удалить старую копию %s: %s", path.name, e)
    return removed


if __name__ == "__main__":
    print("Копия создана:", backup())
