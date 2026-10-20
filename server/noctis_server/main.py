"""Точка входа: фабрика приложения FastAPI. Запуск: python -m noctis_server"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import registry
from .config import settings
from .db import SessionLocal
from .logredact import RedactFilter
from .ratelimit import general_limit
from .security_headers import SecurityHeadersMiddleware
from .routers import admin, auth, device_api, devices, media, ota, personalization, schedule, users, wakewords
from .services import seed_roles, seed_wake_models
from .voice.gateway import Gateway

APP_NAME = "Noctis Voice Server"
VERSION = "0.2.0"


class SiteFiles(StaticFiles):
    """Сайт из папки site/: только страницы, стили, скрипты, картинки и APK (без исходников, тестов и README)."""
    ALLOWED = {".html", ".css", ".js", ".json", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico", ".woff2",
               ".txt", ".xml", ".apk"}

    async def get_response(self, path: str, scope):
        parts = Path(path).parts
        if parts and (parts[0] in {"tests", "__pycache__"} or parts[0].startswith(".")):
            raise StarletteHTTPException(status_code=404)
        suffix = Path(path).suffix.lower()
        if suffix and suffix not in self.ALLOWED:
            raise StarletteHTTPException(status_code=404)
        return await super().get_response(path, scope)


def setup_logging() -> None:
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[logging.FileHandler(log_dir / f"server_{datetime.now():%Y%m%d}.log", encoding="utf-8"),
                  logging.StreamHandler()],
    )
    for handler in logging.getLogger().handlers:       # секреты (client_id в адресах запросов, токены, пароли) в журнал не попадают
        handler.addFilter(RedactFilter())


def create_app(assistant=None) -> FastAPI:
    """assistant — объект голосового конвейера; в тестах подставляется заглушка."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if len(settings.secret_key) < 32:
            raise RuntimeError("Token signing key is too short (NOCTIS_SECRET_KEY must be at least 32 characters)")
        if settings.allow_anonymous_devices:
            logging.getLogger("noctis").warning("Разрешены устройства без токена (NOCTIS_ALLOW_ANONYMOUS_DEVICES=true) — только для старой прошивки")
        with SessionLocal() as db:
            seed_roles(db)
            if settings.seed_wake_models:
                seed_wake_models(db)
        voice = assistant
        if voice is None and settings.load_models:
            from .voice.assistant import NoctisAssistant  # тяжёлые импорты (torch) только здесь
            voice = NoctisAssistant(preload=True)
        app.state.gateway = Gateway(voice)
        if settings.scheduler_enabled:
            app.state.gateway.scheduler.start()
        logging.getLogger("noctis").info("%s %s готов", APP_NAME, VERSION)
        yield
        await app.state.gateway.scheduler.stop()

    app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan,
                  dependencies=[Depends(general_limit)])
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    for router in (auth.router, users.router, devices.router, ota.router, device_api.router, personalization.router, wakewords.router,
                   schedule.router, media.router, admin.router):
        app.include_router(router)

    async def ws_endpoint(websocket: WebSocket):
        await websocket.app.state.gateway.handle(websocket)

    app.add_api_websocket_route(settings.ws_path, ws_endpoint)
    if settings.ws_legacy_path:
        app.add_api_websocket_route(settings.ws_legacy_path, ws_endpoint)  # совместимость со старой прошивкой

    if settings.test_console:
        @app.get("/console", response_class=HTMLResponse, include_in_schema=False)
        async def console():
            """Тестовая страница: регистрация, вход, устройство, настройки, таймеры, музыка."""
            return HTMLResponse((Path(__file__).parent / "static" / "console.html").read_text(encoding="utf-8"),
                                headers={"Cache-Control": "no-store"})

    @app.get("/api", tags=["system"])
    async def root():
        return {"name": APP_NAME, "version": VERSION, "status": "running", "ws_path": settings.ws_path}

    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok", "time": datetime.now().isoformat(),
                "online_devices": len(registry.online_devices)}

    site = Path(settings.site_dir)
    if settings.serve_site and (site / "index.html").is_file():
        app.mount("/", SiteFiles(directory=site, html=True), name="site")  # последним: API-маршруты выше имеют приоритет

    return app
