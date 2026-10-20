"""Заголовки безопасности (этап 8).

Для всех ответов: запрет угадывания типа содержимого, отсутствие Referer, запрет встраивания в чужие страницы, отключение камеры/микрофона/
геолокации в браузере. Для ответов API — ещё `Cache-Control: no-store` (токены и личные данные не оседают в кэше).

Content-Security-Policy — только для страниц сайта: они не содержат встроенных скриптов, обработчиков и стилей (это проверяет тест), поэтому
политика строгая: свои скрипты и стили, картинки со своего адреса и `data:` (значки в CSS), соединения — на любой http(s)-адрес (в кабинете
можно указать адрес сервера). Страницы `/docs` (Swagger грузит скрипты с CDN) и `/console` (встроенный скрипт) — служебные и под CSP не попадают.
"""
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self' http: https:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
BASIC = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
API_PREFIXES = ("/api", "/health", "/noctis", "/nils", "/openapi.json")
SERVICE_PAGES = ("/docs", "/redoc", "/console")


def is_site_page(path: str) -> bool:
    return not path.startswith(API_PREFIXES + SERVICE_PAGES)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")

        async def send_with_headers(message: Message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in BASIC.items():
                    headers.setdefault(key, value)
                if path.startswith("/api"):
                    headers.setdefault("Cache-Control", "no-store")
                if is_site_page(path):
                    headers["Content-Security-Policy"] = CSP
            await send(message)

        await self.app(scope, receive, send_with_headers)
