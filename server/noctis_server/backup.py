"""Резервная копия SQLite: python -m noctis_server.backup  →  data/backups/noctis-<время>.db
Использует встроенный backup API SQLite, поэтому безопасна при работающем сервере."""
import sqlite3
from datetime import datetime

from .config import BASE_DIR, settings


def backup() -> str:
    url = settings.database_url
    if not url.startswith("sqlite:///") or ":memory:" in url:
        raise SystemExit("Резервная копия поддерживается только для файла SQLite")
    src_path = url.removeprefix("sqlite:///")
    dest_dir = BASE_DIR / "data" / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"noctis-{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(src_path) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
    return str(dest)


if __name__ == "__main__":
    print("Копия создана:", backup())
