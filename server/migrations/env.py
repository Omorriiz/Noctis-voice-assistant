from alembic import context
from sqlalchemy import pool

from noctis_server import models  # noqa: F401  (регистрирует таблицы)
from noctis_server.config import settings
from noctis_server.db import Base, make_engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Тесты и __main__ могут передать готовое соединение через config.attributes
    connection = context.config.attributes.get("connection")
    if connection is None:
        engine = make_engine(settings.database_url)
        with engine.connect() as connection:
            _run(connection)
    else:
        _run(connection)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
