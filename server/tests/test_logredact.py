"""Этап 8, пункт 11: секреты не попадают в журналы; в репозитории (дерево и история) нет настоящих секретов."""
import logging
import sys
from pathlib import Path

import pytest

from noctis_server.logredact import RedactFilter, redact

JWT = ".".join(["eyJ" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJ" + "zdWIiOiIxIiwicm9sZSI6InVzZXIifQ", "fake" + "signature" + "0123456789abcdef"])   # собирается на лету: в тексте файла нет JWT


@pytest.mark.parametrize("raw,clean", [
    ('HTTP Request: GET https://api.jamendo.com/v3.0/tracks/?client_id=abcd1234&format=json "HTTP/1.1 200 OK"',
     'HTTP Request: GET https://api.jamendo.com/v3.0/tracks/?client_id=***&format=json "HTTP/1.1 200 OK"'),
    ("ws://192.168.0.136:8000/noctis/v1/?device_id=abc&token=SECRETTOKEN123", "ws://192.168.0.136:8000/noctis/v1/?device_id=abc&token=***"),
    ("password=hunter2hunter2 & more", "password=*** & more"),
    ("Authorization: Bearer abcdefghijklmnop1234567890", "Authorization: ***"),
    (f"token {JWT} issued", "token ***jwt*** issued"),
    ("X-Device-Token: abc123secret", "X-Device-Token: ***"),
])
def test_redact(raw, clean):
    assert redact(raw) == clean


def test_ordinary_messages_are_untouched():
    for msg in ("[c299ed9d] подключено устройство CiO8dOgm75bl (192.168.0.188)", "цикл 5.1 с, первый звук 2.8 с", "Jamendo: ключ не принят"):
        assert redact(msg) == msg


def test_filter_rewrites_formatted_records():
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP Request: GET %s", ("https://x/?client_id=abcd1234",), None)
    assert RedactFilter().filter(rec) is True
    assert rec.getMessage() == "HTTP Request: GET https://x/?client_id=***"


def test_setup_logging_puts_the_filter_on_every_handler(tmp_path, monkeypatch):
    from noctis_server import main
    monkeypatch.setattr(main.settings, "log_dir", str(tmp_path))
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers.clear()
    try:
        main.setup_logging()
        assert root.handlers and all(any(isinstance(f, RedactFilter) for f in h.filters) for h in root.handlers)
        logging.getLogger("httpx").info("GET https://api.jamendo.com/?client_id=abcd1234")
        for h in root.handlers:
            h.flush()
        text = next(tmp_path.glob("server_*.log")).read_text(encoding="utf-8")
        assert "abcd1234" not in text and "client_id=***" in text
    finally:
        for h in root.handlers:
            h.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_no_secrets_in_tracked_files_or_git_history():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import scan_secrets
    rep = scan_secrets.Report()
    files, commits = scan_secrets.scan_git(scan_secrets.real_secrets(), rep)
    assert files > 100 and commits > 5
    assert rep.hard == [], rep.hard
