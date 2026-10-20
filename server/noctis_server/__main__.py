"""python -m noctis_server — запуск сервера (применяет миграции БД, затем стартует uvicorn)."""
import socket

import uvicorn
from alembic import command
from alembic.config import Config

from .config import BASE_DIR, settings
from .main import create_app, setup_logging


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def migrate() -> None:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    setup_logging()
    (BASE_DIR / "data").mkdir(exist_ok=True)
    migrate()
    ip = local_ip()
    print(f"\nNOCTIS — голосовой сервер\n  WebSocket: ws://{ip}:8000{settings.ws_path}\n"
          f"  REST API:  http://{ip}:8000/docs\n")
    uvicorn.run(create_app(), host="0.0.0.0", port=8000, ws_ping_interval=20.0, ws_ping_timeout=60.0)
