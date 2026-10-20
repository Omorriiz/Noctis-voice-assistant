"""Местное время устройства и фразы о времени/дате на 5 языках (словами — для TTS)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .numwords import agree, count_phrase, to_words

DEFAULT_TZ = "Europe/Moscow"

MONTHS = {
    "ru": "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split(),
    "en": "January February March April May June July August September October November December".split(),
    "es": "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(),
    "de": "Januar Februar März April Mai Juni Juli August September Oktober November Dezember".split(),
    "fr": "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split(),
}
WEEKDAYS = {  # понедельник = 0
    "ru": "понедельник вторник среда четверг пятница суббота воскресенье".split(),
    "en": "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split(),
    "es": "lunes martes miércoles jueves viernes sábado domingo".split(),
    "de": "Montag Dienstag Mittwoch Donnerstag Freitag Samstag Sonntag".split(),
    "fr": "lundi mardi mercredi jeudi vendredi samedi dimanche".split(),
}
# ординалы дня месяца (ru: «пятнадцатое», en: «fifteenth»)
_RU_DAY = ("первое второе третье четвёртое пятое шестое седьмое восьмое девятое десятое одиннадцатое двенадцатое "
           "тринадцатое четырнадцатое пятнадцатое шестнадцатое семнадцатое восемнадцатое девятнадцатое двадцатое "
           "двадцать_первое двадцать_второе двадцать_третье двадцать_четвёртое двадцать_пятое двадцать_шестое "
           "двадцать_седьмое двадцать_восьмое двадцать_девятое тридцатое тридцать_первое").split()
_EN_DAY = ("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth "
           "fifteenth sixteenth seventeenth eighteenth nineteenth twentieth twenty_first twenty_second twenty_third "
           "twenty_fourth twenty_fifth twenty_sixth twenty_seventh twenty_eighth twenty_ninth thirtieth thirty_first").split()
_DE_SPECIAL = {1: "erste", 3: "dritte", 7: "siebte", 8: "achte"}


def zone(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo(DEFAULT_TZ)


def local_now(tz_name: str | None, now_utc: datetime | None = None) -> datetime:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(zone(tz_name))


def utc_offset_sec(tz_name: str | None, now_utc: datetime | None = None) -> int:
    return int(local_now(tz_name, now_utc).utcoffset().total_seconds())


def _de_day(n: int) -> str:
    if n in _DE_SPECIAL:
        return _DE_SPECIAL[n]
    from .numwords import to_words as w
    base = w(n, "de")
    return base + ("te" if n < 20 else "ste")


def day_words(day: int, lang: str) -> str:
    if lang == "ru":
        return _RU_DAY[day - 1].replace("_", " ")
    if lang == "en":
        return _EN_DAY[day - 1].replace("_", " ")
    if lang == "es":
        return "primero" if day == 1 else to_words(day, "es")
    if lang == "de":
        return _de_day(day)
    return "premier" if day == 1 else to_words(day, "fr")


def part_of_day(hour: int, lang: str) -> str:
    key = "night" if hour < 5 else "morning" if hour < 12 else "afternoon" if hour < 18 else "evening" if hour < 23 else "night"
    return {
        "en": {"night": "at night", "morning": "in the morning", "afternoon": "in the afternoon", "evening": "in the evening"},
        "es": {"night": "de la noche", "morning": "de la mañana", "afternoon": "de la tarde", "evening": "de la noche"},
    }[lang][key]


def time_words(dt: datetime, lang: str) -> str:
    """«двадцать два часа пятнадцать минут» / «ten fifteen in the evening» (само время, без «Сейчас»)."""
    h, m = dt.hour, dt.minute
    if lang == "ru":
        s = count_phrase(h, "hour", "ru")
        return s if m == 0 else f"{s} {count_phrase(m, 'minute', 'ru')}"
    if lang == "de":
        return f"{to_words(h, 'de')} Uhr" + (f" {to_words(m, 'de')}" if m else "")
    if lang == "fr":
        return agree(to_words(h, "fr"), "fr", True) + (" heure" if h in (0, 1) else " heures") + (f" {to_words(m, 'fr')}" if m else "")
    h12 = h % 12 or 12
    if lang == "en":
        mins = "" if m == 0 else (f" oh {to_words(m, 'en')}" if m < 10 else f" {to_words(m, 'en')}")
        return f"{to_words(h12, 'en')}{mins} {part_of_day(h, 'en')}" if m or h not in (0, 12) else \
            ("midnight" if h == 0 else "noon")
    # es
    start = "la una" if h12 == 1 else f"las {to_words(h12, 'es')}"
    return f"{start}" + (f" y {to_words(m, 'es')}" if m else " en punto") + f" {part_of_day(h, 'es')}"


def time_phrase(dt: datetime, lang: str) -> str:
    t = time_words(dt, lang)
    if lang == "ru":
        return f"Сейчас {t}."
    if lang == "en":
        return f"It's {t}."
    if lang == "es":
        return f"Son {t}." if not t.startswith("la una") else f"Es {t}."
    if lang == "de":
        return f"Es ist {t}."
    return f"Il est {t}."


def date_phrase(dt: datetime, lang: str) -> str:
    wd, mo, d = WEEKDAYS[lang][dt.weekday()], MONTHS[lang][dt.month - 1], dt.day
    dw = day_words(d, lang)
    if lang == "ru":
        return f"Сегодня {wd}, {dw} {mo}."
    if lang == "en":
        return f"Today is {wd}, {mo} {dw}."
    if lang == "es":
        return f"Hoy es {wd}, {dw} de {mo}."
    if lang == "de":
        return f"Heute ist {wd}, der {dw} {mo}."
    return f"Nous sommes {wd} {dw} {mo}."


def alarm_time_words(hour: int, minute: int, lang: str) -> str:
    return time_words(datetime(2000, 1, 1, hour, minute), lang)


def weekday_list_words(days: list[int], lang: str) -> str:
    days = sorted(set(days))
    if days == [0, 1, 2, 3, 4]:
        return {"ru": "по будням", "en": "on weekdays", "es": "los días laborables", "de": "an Werktagen",
                "fr": "en semaine"}[lang]
    if days == [5, 6]:
        return {"ru": "по выходным", "en": "on weekends", "es": "los fines de semana", "de": "am Wochenende",
                "fr": "le week-end"}[lang]
    if days == list(range(7)):
        return {"ru": "каждый день", "en": "every day", "es": "todos los días", "de": "jeden Tag",
                "fr": "tous les jours"}[lang]
    return ", ".join(WEEKDAYS[lang][d] for d in days)
