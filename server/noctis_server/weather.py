"""Погода на сегодня: Open-Meteo (open-meteo.com) — бесплатно для некоммерческого использования, без ключа и регистрации.

Два запроса: поиск места (geocoding-api.open-meteo.com) и прогноз (api.open-meteo.com). В запрос уходит только название места —
никаких данных о пользователе и устройстве. Данные Open-Meteo распространяются по лицензии CC BY 4.0 (ссылка на сайте).

Особенности, выявленные на настоящем API (19.10.2026):
  * поиск понимает только начальную форму названия: «в Москве» даёт село в Нью-Мексико, «в Казани» — ничего. Для русского
    название приводится к именительному падежу (pymorphy3), затем из найденных мест выбирается самое населённое;
  * ответы иногда не приходят (таймаут TLS) — запросы идут парой параллельно, до двух раз;
  * озвучиваемое название склоняется («в Москве», «во Владивостоке») тем же pymorphy3.
Блокирующий код: из асинхронного кода вызывать через asyncio.to_thread.
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import httpx

from .numwords import agree, noun, to_words

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 6.0
PARALLEL = 2
ROUNDS = 2
GEO_TTL_SEC = 24 * 3600
FORECAST_TTL_SEC = 15 * 60
transport: httpx.BaseTransport | None = None      # подменяется в тестах

_cache: dict = {}
_cache_lock = threading.Lock()


class WeatherError(RuntimeError):
    """Сеть/сервис недоступны."""


class PlaceNotFound(WeatherError):
    pass


@dataclass
class Place:
    name: str            # название на языке пользователя (из поиска)
    latitude: float
    longitude: float
    country: str = ""
    timezone: str = ""
    kind: str = ""       # feature_code Open-Meteo (PCLI — страна)


# ------------------------------------------------------------------ сеть
def _get_json(url: str, params: dict) -> dict:
    last: Exception | None = None
    for _ in range(ROUNDS):
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            def one():
                with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
                    r = client.get(url, params=params)
                    r.raise_for_status()
                    return r.json()
            futures = [pool.submit(one) for _ in range(PARALLEL)]
            for fut in as_completed(futures):
                try:
                    body = fut.result()
                except (httpx.HTTPError, ValueError) as e:
                    last = e
                    continue
                for other in futures:
                    other.cancel()
                return body
    raise WeatherError(str(last) if last else "no answer")


def _cached(key, ttl, make):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = make()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


# ------------------------------------------------------------------ русская морфология
_morph = None
_morph_lock = threading.Lock()
_NO_INFLECT_AFTER = {"на", "под", "над", "в", "во", "у", "при", "по"}      # «Ростов-на-Дону»: после предлога слово не меняется
ALIASES_RU = {"питер": "санкт петербург", "питере": "санкт петербург", "питера": "санкт петербург", "спб": "санкт петербург",
              "петербург": "санкт петербург", "петербурге": "санкт петербург", "петербурга": "санкт петербург",
              "мск": "москва", "екб": "екатеринбург", "нск": "новосибирск", "нижний": "нижний новгород"}


def _analyzer():
    global _morph
    with _morph_lock:
        if _morph is None:
            import pymorphy3
            _morph = pymorphy3.MorphAnalyzer()
        return _morph


def to_nominative_ru(name: str) -> list[str]:
    """Варианты названия в именительном падеже («нижнем новгороде» → «нижний новгород») в порядке предпочтения."""
    key = " ".join(name.split())
    if key in ALIASES_RU:
        return [ALIASES_RU[key]]
    morph = _analyzer()
    tokens = key.split()
    out, after_prep = [], False
    for tok in tokens:
        if after_prep or tok in _NO_INFLECT_AFTER:
            out.append(tok)
            after_prep = tok in _NO_INFLECT_AFTER
            continue
        word = tok
        for p in morph.parse(tok)[:3]:
            try:
                n = p.inflect({"nomn"})
            except Exception:
                n = None
            if n:
                word = n.word
                break
        out.append(word)
    variants = [" ".join(out)]
    if key not in variants:
        variants.append(key)
    return variants


def _candidates(name: str, lang: str) -> list[str]:
    name = " ".join(name.split())
    if not name:
        return []
    return to_nominative_ru(name) if lang == "ru" else [name]


# ------------------------------------------------------------------ поиск места
def find_place(name: str, lang: str = "en") -> Place:
    """Самое населённое место с таким названием. PlaceNotFound — нет такого, WeatherError — сервис недоступен."""
    for cand in _candidates(name, lang):
        key = ("geo", lang, cand)

        def make(cand=cand):
            body = _get_json(GEOCODE_URL, {"name": cand, "count": 10, "language": lang, "format": "json"})
            return body.get("results") or []

        results = _cached(key, GEO_TTL_SEC, make)
        if results:
            best = max(results, key=lambda r: r.get("population") or 0)
            return Place(name=str(best.get("name", cand)), latitude=float(best["latitude"]), longitude=float(best["longitude"]),
                         country=str(best.get("country", "")), timezone=str(best.get("timezone", "")),
                         kind=str(best.get("feature_code", "")))
    raise PlaceNotFound(name)


def place_from_timezone(tz: str, lang: str = "en") -> Place | None:
    """Запасной город, если «домашний» не задан: последняя часть пояса («Europe/Moscow» → Moscow)."""
    city = (tz or "").rsplit("/", 1)[-1].replace("_", " ").strip()
    if not city or city.upper() == city:
        return None
    try:
        return find_place(city, lang)
    except WeatherError:
        return None


# ------------------------------------------------------------------ прогноз
def forecast(place: Place) -> dict:
    """Данные на сегодня: now_temp, high, low, code, precip_prob, precip_sum, wind_ms."""
    key = ("fc", round(place.latitude, 2), round(place.longitude, 2))

    def make():
        body = _get_json(FORECAST_URL, {
            "latitude": place.latitude, "longitude": place.longitude, "timezone": "auto", "forecast_days": 1,
            "wind_speed_unit": "ms",
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max"})
        daily, cur = body.get("daily") or {}, body.get("current") or {}

        def first(key):
            v = daily.get(key) or [None]
            return v[0]

        if first("temperature_2m_max") is None or first("temperature_2m_min") is None:
            raise WeatherError("no daily data")
        return {"now_temp": cur.get("temperature_2m"), "high": first("temperature_2m_max"), "low": first("temperature_2m_min"),
                "code": int(first("weather_code") if first("weather_code") is not None else (cur.get("weather_code") or 0)),
                "precip_prob": first("precipitation_probability_max"), "precip_sum": first("precipitation_sum") or 0.0,
                "wind_ms": first("wind_speed_10m_max") if first("wind_speed_10m_max") is not None else cur.get("wind_speed_10m")}

    return _cached(key, FORECAST_TTL_SEC, make)


# ------------------------------------------------------------------ фразы
def _L(ru, en, es, de, fr):
    return {"ru": ru, "en": en, "es": es, "de": de, "fr": fr}


DESC = {   # код WMO → ключ описания
    **{0: "clear", 1: "mostly_clear", 2: "partly_cloudy", 3: "overcast", 45: "fog", 48: "fog", 51: "drizzle", 53: "drizzle",
       55: "drizzle", 56: "freezing_rain", 57: "freezing_rain", 61: "rain", 63: "rain", 65: "heavy_rain", 66: "freezing_rain",
       67: "freezing_rain", 71: "snow", 73: "snow", 75: "heavy_snow", 77: "snow", 80: "showers", 81: "showers", 82: "heavy_rain",
       85: "snow_showers", 86: "snow_showers", 95: "thunder", 96: "hail_thunder", 99: "hail_thunder"}}
TEXT = {
    "clear": _L("ясно", "clear skies", "despejado", "klar", "ciel dégagé"),
    "mostly_clear": _L("преимущественно ясно", "mostly clear", "mayormente despejado", "überwiegend klar", "plutôt dégagé"),
    "partly_cloudy": _L("переменная облачность", "partly cloudy", "parcialmente nublado", "teils bewölkt", "partiellement nuageux"),
    "overcast": _L("пасмурно", "overcast", "nublado", "bedeckt", "couvert"),
    "fog": _L("туман", "fog", "niebla", "Nebel", "brouillard"),
    "drizzle": _L("морось", "drizzle", "llovizna", "Nieselregen", "bruine"),
    "rain": _L("дождь", "rain", "lluvia", "Regen", "pluie"),
    "heavy_rain": _L("сильный дождь", "heavy rain", "lluvia fuerte", "starker Regen", "forte pluie"),
    "freezing_rain": _L("ледяной дождь", "freezing rain", "lluvia helada", "gefrierender Regen", "pluie verglaçante"),
    "snow": _L("снег", "snow", "nieve", "Schnee", "neige"),
    "heavy_snow": _L("сильный снегопад", "heavy snow", "nevada fuerte", "starker Schneefall", "forte neige"),
    "showers": _L("ливни", "rain showers", "chubascos", "Regenschauer", "averses"),
    "snow_showers": _L("снегопады", "snow showers", "chubascos de nieve", "Schneeschauer", "averses de neige"),
    "thunder": _L("гроза", "thunderstorms", "tormenta", "Gewitter", "orage"),
    "hail_thunder": _L("гроза с градом", "thunderstorms with hail", "tormenta con granizo", "Gewitter mit Hagel", "orage avec grêle"),
}
UNIT = {
    "degree": {"ru": ("градус", "градуса", "градусов"), "en": ("degree", "degrees"), "es": ("grado", "grados"),
               "de": ("Grad", "Grad"), "fr": ("degré", "degrés")},
    "percent": {"ru": ("процент", "процента", "процентов"), "en": ("percent", "percent"), "es": ("por ciento", "por ciento"),
                "de": ("Prozent", "Prozent"), "fr": ("pour cent", "pour cent")},
    "ms": {"ru": ("метр в секунду", "метра в секунду", "метров в секунду"), "en": ("meter per second", "meters per second"),
           "es": ("metro por segundo", "metros por segundo"), "de": ("Meter pro Sekunde", "Meter pro Sekunde"),
           "fr": ("mètre par seconde", "mètres par seconde")},
    "kmh": {"ru": ("километр в час", "километра в час", "километров в час"), "en": ("kilometer per hour", "kilometers per hour"),
            "es": ("kilómetro por hora", "kilómetros por hora"), "de": ("Kilometer pro Stunde", "Kilometer pro Stunde"),
            "fr": ("kilomètre par heure", "kilomètres par heure")},
}
MINUS = _L("минус", "minus", "menos", "minus", "moins")
FORMS = {   # шаблоны фраз; {place} уже с предлогом
    "head": _L("Сегодня {place} {desc}.", "Today {place}, {desc}.", "Hoy {place}: {desc}.", "Heute {place}: {desc}.",
               "Aujourd'hui {place} : {desc}."),
    "temps": _L("Сейчас {now}, днём {hi}, ночью {lo}.", "Right now it is {now}, with a high of {hi} and a low of {lo}.",
                "Ahora hay {now}, máxima de {hi} y mínima de {lo}.", "Jetzt sind es {now}, tagsüber {hi}, nachts {lo}.",
                "Il fait actuellement {now}, maximum {hi}, minimum {lo}."),
    "temps_no_now": _L("Днём {hi}, ночью {lo}.", "A high of {hi} and a low of {lo}.", "Máxima de {hi} y mínima de {lo}.",
                       "Tagsüber {hi}, nachts {lo}.", "Maximum {hi}, minimum {lo}."),
    "dry": _L("Осадков не ожидается.", "No precipitation expected.", "No se esperan precipitaciones.", "Kein Niederschlag erwartet.",
              "Aucune précipitation prévue."),
    "maybe": _L("Возможны осадки, вероятность {p}.", "Precipitation is possible, {p} chance.", "Posibles precipitaciones, probabilidad de {p}.",
                "Niederschlag möglich, Wahrscheinlichkeit {p}.", "Précipitations possibles, probabilité de {p}."),
    "likely": _L("Вероятны осадки, вероятность {p}.", "Precipitation is likely, {p} chance.", "Probables precipitaciones, probabilidad de {p}.",
                 "Niederschlag wahrscheinlich, Wahrscheinlichkeit {p}.", "Précipitations probables, probabilité de {p}."),
    "wind": _L("Скорость ветра {w}.", "Wind up to {w}.", "Viento de hasta {w}.", "Wind bis zu {w}.", "Vent jusqu'à {w}."),
    "calm": _L("Ветра почти нет.", "Almost no wind.", "Casi sin viento.", "Fast windstill.", "Presque pas de vent."),
}


def _count(n: int, unit: str, lang: str) -> str:
    n = abs(int(n))
    forms = UNIT[unit][lang]
    if lang == "ru":
        word = forms[0] if n % 10 == 1 and n % 100 != 11 else forms[1] if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else forms[2]
    else:
        word = forms[0] if n == 1 else forms[1]
    return f"{agree(to_words(n, lang), lang, False)} {word}"


def _temp(value: float, lang: str) -> str:
    n = int(round(value))
    sign = MINUS[lang] + " " if n < 0 else ""
    return sign + _count(n, "degree", lang)


# предложный падеж названия по-русски
def _prep_ru(name: str) -> str:
    """«Москва» → «Москве», «Нижний Новгород» → «Нижнем Новгороде», «Ростов-на-Дону» → «Ростове-на-Дону», «Лос-Анджелес» → «Лос-Анджелесе»:
    склоняется главное слово (последнее до предлога) и прилагательные перед ним; остальное не меняется."""
    morph = _analyzer()
    parts = re.split(r"([ \-])", name)
    words = [i for i, p in enumerate(parts) if p not in (" ", "-") and p]
    head = len(words) - 1
    for pos, i in enumerate(words):
        if parts[i].lower() in _NO_INFLECT_AFTER and pos > 0:
            head = pos - 1
            break
    plural = False                             # «Великие Луки»: прилагательное во множественном числе → и слово при нём
    for pos, i in enumerate(words[:head + 1]):
        tok = parts[i]
        parses = morph.parse(tok)[:4]
        if plural:
            parses = sorted(parses, key=lambda p: "plur" not in p.tag)
        for p in parses:
            if pos < head and "ADJF" not in p.tag:
                continue                       # перед главным словом склоняем только прилагательные (не «лос» из «Лос-Анджелеса»)
            try:
                f = p.inflect({"loct", "plur"} if ("plur" in p.tag or plural) else {"loct"})
            except Exception:
                f = None
            if f:
                if pos < head and "plur" in p.tag:
                    plural = True
                parts[i] = f.word.capitalize() if tok[:1].isupper() else f.word
                break
    return "".join(parts)


def _in_place(place: Place, lang: str) -> str:
    name = place.name
    if lang == "ru":
        low = name.lower()
        prep = "во" if len(low) > 1 and low[0] in "вф" and low[1] not in "аеёиоуыэюяй" else "в"
        return f"{prep} {_prep_ru(name)}"
    if lang == "fr":
        return f"pour {name}" if place.kind == "PCLI" else f"à {name}"
    return f"in {name}" if lang in ("en", "de") else f"en {name}"


def describe(place: Place, data: dict, lang: str) -> str:
    """Фраза для озвучивания (числа словами)."""
    desc = TEXT[DESC.get(data["code"], "partly_cloudy")][lang]
    parts = [FORMS["head"][lang].format(place=_in_place(place, lang), desc=desc)]
    hi, lo = _temp(data["high"], lang), _temp(data["low"], lang)
    if data.get("now_temp") is not None:
        parts.append(FORMS["temps"][lang].format(now=_temp(data["now_temp"], lang), hi=hi, lo=lo))
    else:
        parts.append(FORMS["temps_no_now"][lang].format(hi=hi, lo=lo))
    prob = data.get("precip_prob")
    prob = int(prob) if prob is not None else (60 if (data.get("precip_sum") or 0) >= 1 else 0)
    if prob < 20 and (data.get("precip_sum") or 0) < 1:
        parts.append(FORMS["dry"][lang])
    else:
        p = _count(max(prob, 20), "percent", lang)
        parts.append(FORMS["likely" if prob >= 50 else "maybe"][lang].format(p=p))
    wind = data.get("wind_ms")
    if wind is not None:
        if wind < 1:
            parts.append(FORMS["calm"][lang])
        elif lang == "ru":
            parts.append(FORMS["wind"][lang].format(w=_count(round(wind), "ms", lang)))
        else:
            parts.append(FORMS["wind"][lang].format(w=_count(round(wind * 3.6), "kmh", lang)))
    return " ".join(parts)
