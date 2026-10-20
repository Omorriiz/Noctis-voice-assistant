"""Миграция создаёт ровно ту схему, что описана в моделях, и откатывается."""
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from noctis_server.config import BASE_DIR
from noctis_server.db import Base
from noctis_server import models  # noqa: F401

REQUIRED = {"users", "devices", "user_devices", "settings", "timers", "alarms", "media", "playlists",
            "history", "mqtt_topics", "ota_versions", "sessions", "roles", "user_settings"}


def test_alembic_upgrade_and_downgrade(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        tables = set(inspect(conn).get_table_names())
        assert REQUIRED <= tables
        assert tables - {"alembic_version"} == set(Base.metadata.tables)
        command.downgrade(cfg, "base")
        assert set(inspect(conn).get_table_names()) <= {"alembic_version"}


def test_sqlite_pragmas_and_backup(tmp_path, monkeypatch):
    import sqlite3
    from noctis_server import backup as bk
    from noctis_server.db import make_engine

    db_file = tmp_path / "n.db"
    engine = make_engine(f"sqlite:///{db_file.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
    engine.dispose()

    monkeypatch.setattr(bk.settings, "database_url", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr(bk, "BASE_DIR", tmp_path)
    copy = bk.backup()
    assert "users" in {r[0] for r in sqlite3.connect(copy).execute("select name from sqlite_master")}


def test_migrated_columns_match_models(tmp_path):
    """Миграции (включая 0003 этапа 5) дают те же столбцы, что описаны в моделях."""
    engine = create_engine(f"sqlite:///{tmp_path / 'cols.db'}")
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        insp = inspect(conn)
        for name, table in Base.metadata.tables.items():
            assert {c["name"] for c in insp.get_columns(name)} == {c.name for c in table.columns}, name
        assert {"finished_at"} <= {c["name"] for c in insp.get_columns("timers")}
        assert {"last_fired_key"} <= {c["name"] for c in insp.get_columns("alarms")}
        assert {"album", "genre", "external_id"} <= {c["name"] for c in insp.get_columns("media")}
        command.downgrade(cfg, "0002")
        assert "finished_at" not in {c["name"] for c in inspect(conn).get_columns("timers")}


def test_migration_0005_moves_removed_wake_words_to_nils(tmp_path):
    """Колонки со словами, которых больше нет («jarvis», своя фраза), возвращаются на «nils» с новой ревизией; «alexa» остаётся."""
    from sqlalchemy import text
    engine = create_engine(f"sqlite:///{tmp_path / 'w.db'}")
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0004")
        for i, word in enumerate(("nils", "alexa", "jarvis", "custom:Ноктис", "privet_esp"), start=1):
            conn.execute(text("INSERT INTO devices (id, public_id, token_hash, name, firmware_version, last_ip, enabled, created_at) "
                              "VALUES (:i, :p, 'x', 'd', '', '', 1, '2026-10-19')"), {"i": i, "p": f"dev{i}"})
            conn.execute(text("INSERT INTO settings (device_id, wake_word, voice_gender, character_id, language, volume, timezone, revision) "
                              "VALUES (:i, :w, 'male', 'friendly', 'ru', 70, 'Europe/Moscow', 3)"), {"i": i, "w": word})
        conn.execute(text("INSERT INTO wake_models (id, wake_word, label, sha256, size_bytes, filename, notes, created_at) VALUES "
                          "(1, 'nils', 'n', 'a', 1, 'a.nwwm', '', '2026-10-19'), "
                          "(2, 'custom:Ноктис', 'c', 'b', 1, 'b.nwwm', '', '2026-10-19')"))
        command.upgrade(cfg, "head")
        rows = {r[0]: (r[1], r[2]) for r in conn.execute(text("SELECT device_id, wake_word, revision FROM settings"))}
        assert rows == {1: ("nils", 3), 2: ("alexa", 3), 3: ("nils", 4), 4: ("nils", 4), 5: ("nils", 4)}
        assert [r[0] for r in conn.execute(text("SELECT wake_word FROM wake_models"))] == ["nils"]
