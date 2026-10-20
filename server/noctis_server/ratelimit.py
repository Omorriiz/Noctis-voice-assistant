"""Простое ограничение частоты запросов по IP (скользящее окно, в памяти процесса).
Для нескольких воркеров/продакшна заменяется на Redis (этап 8)."""
import time
from collections import defaultdict, deque

from fastapi import HTTPException
from starlette.requests import HTTPConnection as Request  # и для HTTP, и для WebSocket

from .config import settings


class RateLimiter:
    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str, limit: int) -> None:
        now = time.monotonic()
        window = self.hits[key]
        while window and now - window[0] > settings.rate_limit_window_sec:
            window.popleft()
        if len(window) >= limit:
            raise HTTPException(429, "Too many requests", headers={"Retry-After": str(settings.rate_limit_window_sec)})
        window.append(now)

    def reset(self):
        self.hits.clear()


limiter = RateLimiter()


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def general_limit(request: Request):
    limiter.check(f"g:{_ip(request)}", settings.rate_limit_requests)


def auth_limit(request: Request):
    """Строже для входа/регистрации/сброса пароля (защита от перебора)."""
    limiter.check(f"a:{_ip(request)}", settings.rate_limit_auth_requests)
