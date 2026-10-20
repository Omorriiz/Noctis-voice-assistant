"""Скрытие секретов в журналах (этап 8): ключи в адресах запросов (client_id=…), токены, пароли, JWT, Bearer-заголовки.

Фильтр вешается на обработчики журнала, поэтому секрет не попадает ни в файл, ни в консоль, откуда бы его ни записали
(в том числе библиотеки: httpx пишет адрес каждого запроса целиком — вместе с client_id Jamendo).
"""
import logging
import re

_RULES = [
    (re.compile(r"(?i)\b(client_id|client_secret|api[_-]?key|access_token|refresh_token|token|password|passwd|secret|key)=([^&\s\"'<>]+)"), r"\1=***"),
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9_\-.=+/]{12,}"), r"\1 ***"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "***jwt***"),
    (re.compile(r"(?i)\b(x-device-token|authorization)\s*[:=]\s*(?:(?:Bearer|Basic)\s+)?\S+"), r"\1: ***"),
]


def redact(text: str) -> str:
    for rx, repl in _RULES:
        text = rx.sub(repl, text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True
