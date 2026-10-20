"""Подключение к БД: SQLite через SQLAlchemy 2 (один файл data/noctis.db, без отдельного сервера).
Схема написана переносимо, поэтому при необходимости URL можно заменить на другую СУБД."""
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")    # по умолчанию в SQLite выключены
            cur.execute("PRAGMA busy_timeout=5000")  # ждать блокировку, а не падать
            if ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")    # чтение не блокирует запись
                cur.execute("PRAGMA synchronous=NORMAL")  # безопасно при WAL, быстрее
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
