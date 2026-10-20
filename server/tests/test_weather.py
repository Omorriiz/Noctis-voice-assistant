"""Погода: разбор фраз на 5 языках, поиск места со склонениями, фразы, домашний город, навык целиком (сеть подменена записанными ответами)."""
import asyncio

import httpx
import pytest

from noctis_server import weather
from noctis_server.intents import parse
from noctis_server.skills import Context, run

GEO_MOSCOW = {"results": [
    {"id": 524901, "name": "Москва", "latitude": 55.75, "longitude": 37.62, "country": "Россия", "country_code": "RU", "population": 10381222,
     "timezone": "Europe/Moscow", "feature_code": "PPLC"},
    {"id": 5601538, "name": "Москва", "latitude": 46.73, "longitude": -117.0, "country": "США", "country_code": "US", "population": 25060,
     "timezone": "America/Los_Angeles", "feature_code": "PPLA2"}]}
FORECAST = {"current": {"temperature_2m": 6.3, "weather_code": 0, "wind_speed_10m": 2.9},
            "daily": {"weather_code": [3], "temperature_2m_max": [11.9], "temperature_2m_min": [-2.4], "precipitation_probability_max": [10],
                      "precipitation_sum": [0.0], "wind_speed_10m_max": [3.54]}}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    weather._cache.clear()
    monkeypatch.setattr(weather, "transport", None)


def serve(monkeypatch, geo=None, fc=None, calls=None):
    """Подменяет Open-Meteo: geo — словарь «запрошенное имя → ответ», fc — ответ прогноза."""
    def handler(request: httpx.Request):
        if calls is not None:
            calls.append(str(request.url))
        if request.url.host.startswith("geocoding"):
            name = request.url.params["name"]
            return httpx.Response(200, json=(geo or {}).get(name, {}))
        return httpx.Response(200, json=fc or FORECAST)
    monkeypatch.setattr(weather, "transport", httpx.MockTransport(handler))


# ---------------------------------------------------------------- разбор фраз
@pytest.mark.parametrize("lang,text,place", [
    ("ru", "Какая погода в Казани", "казани"), ("ru", "какая сегодня погода в нижнем новгороде", "нижнем новгороде"),
    ("ru", "Погода в Санкт-Петербурге сегодня пожалуйста", "санкт петербурге"), ("ru", "какая погода", ""),
    ("ru", "какая погода на улице", ""), ("ru", "Нильс, какая погода в Ростове-на-Дону?", "ростове на дону"),
    ("ru", "сколько градусов на улице", ""), ("ru", "будет ли дождь в Сочи", "сочи"), ("ru", "погода в казани на сегодня", "казани"),
    ("en", "What's the weather like in London today?", "london"), ("en", "weather in New York", "new york"), ("en", "how is the weather", ""),
    ("en", "will it rain in Paris", "paris"),
    ("es", "¿Qué tiempo hace en Madrid?", "madrid"), ("es", "el tiempo en Sevilla hoy", "sevilla"), ("es", "¿va a llover?", ""),
    ("de", "Wie ist das Wetter in Köln heute?", "koln"), ("de", "Wetter in München", "munchen"), ("de", "wird es regnen", ""),
    ("fr", "Quelle est la météo à Paris aujourd'hui ?", "paris"), ("fr", "quel temps fait-il à Lyon", "lyon"), ("fr", "la météo", ""),
])
def test_weather_phrases(lang, text, place):
    it = parse(text, lang)
    assert it and it.kind == "weather" and it.params["place"] == place and it.params["day"] == 0


def test_tomorrow_is_recognised_and_other_requests_untouched():
    assert parse("погода завтра в Москве", "ru").params == {"place": "москве", "day": 1}
    assert parse("какая погода послезавтра", "ru").params["day"] == 2
    assert parse("расскажи про погоду в средние века в Европе и в Азии и ещё в Африке", "ru") is None      # длинный оборот — LLM
    assert parse("включи музыку", "ru").kind == "music_play" and parse("который час", "ru").kind == "time"
    assert parse("Расскажи о климате Марса", "ru") is None


# ---------------------------------------------------------------- место
def test_inflected_name_is_returned_to_nominative_and_most_populous_chosen(monkeypatch):
    calls = []
    serve(monkeypatch, geo={"москва": GEO_MOSCOW}, calls=calls)
    p = weather.find_place("москве", "ru")
    assert (p.name, p.country, p.timezone) == ("Москва", "Россия", "Europe/Moscow") and p.latitude == 55.75      # не Москва в Айдахо
    assert any("name=%D0%BC%D0%BE%D1%81%D0%BA%D0%B2%D0%B0" in c for c in calls)


@pytest.mark.parametrize("said,nominative", [("москве", "москва"), ("казани", "казань"), ("нижнем новгороде", "нижний новгород"),
                                             ("санкт петербурге", "санкт петербург"), ("владивостоке", "владивосток"), ("питере", "санкт петербург"),
                                             ("ростове на дону", "ростов на дону"), ("лондоне", "лондон")])
def test_to_nominative_ru(said, nominative):
    assert weather.to_nominative_ru(said)[0] == nominative


def test_unknown_place_and_service_failures(monkeypatch):
    serve(monkeypatch, geo={})
    with pytest.raises(weather.PlaceNotFound):
        weather.find_place("абракадабра", "ru")
    monkeypatch.setattr(weather, "transport", httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(weather.WeatherError):
        weather.find_place("лондон", "en")


def test_place_from_timezone(monkeypatch):
    serve(monkeypatch, geo={"Moscow": GEO_MOSCOW})
    assert weather.place_from_timezone("Europe/Moscow", "en").name == "Москва"
    assert weather.place_from_timezone("UTC", "en") is None and weather.place_from_timezone("", "ru") is None


# ---------------------------------------------------------------- фразы
def place(name="Москва", kind="PPLC"):
    return weather.Place(name, 55.75, 37.62, kind=kind)


def test_russian_phrase():
    text = weather.describe(place(), {"now_temp": 6.3, "high": 11.9, "low": -2.4, "code": 3, "precip_prob": 10, "precip_sum": 0, "wind_ms": 3.54}, "ru")
    assert text == ("Сегодня в Москве пасмурно. Сейчас шесть градусов, днём двенадцать градусов, ночью минус два градуса. "
                    "Осадков не ожидается. Скорость ветра четыре метра в секунду.")
    assert not any(ch.isdigit() for ch in text)           # числа только словами (для озвучивания)


@pytest.mark.parametrize("n,expected", [(1, "один градус"), (2, "два градуса"), (5, "пять градусов"), (11, "одиннадцать градусов"),
                                        (21, "двадцать один градус"), (0, "ноль градусов"), (-1, "минус один градус"), (-5, "минус пять градусов")])
def test_russian_degree_agreement(n, expected):
    assert weather._temp(n, "ru") == expected


@pytest.mark.parametrize("name,expected", [("Москва", "в Москве"), ("Нижний Новгород", "в Нижнем Новгороде"), ("Ростов-на-Дону", "в Ростове-на-Дону"),
                                           ("Владивосток", "во Владивостоке"), ("Лос-Анджелес", "в Лос-Анджелесе"), ("Великие Луки", "в Великих Луках"),
                                           ("Сочи", "в Сочи"), ("Франция", "во Франции")])
def test_russian_prepositional(name, expected):
    assert weather._in_place(place(name), "ru") == expected


@pytest.mark.parametrize("lang,start", [("en", "Today in Moscow, "), ("es", "Hoy en Moscow: "), ("de", "Heute in Moscow: "), ("fr", "Aujourd'hui à Moscow : ")])
def test_other_languages(lang, start):
    text = weather.describe(place("Moscow"), {"now_temp": -3.0, "high": 1.0, "low": -6.0, "code": 71, "precip_prob": 80, "precip_sum": 3.0,
                                              "wind_ms": 5.0}, lang)
    assert text.startswith(start) and not any(ch.isdigit() for ch in text)
    assert {"en": "minus", "es": "menos", "de": "minus", "fr": "moins"}[lang] in text


def test_rain_probability_and_calm_wind_and_missing_values():
    base = {"now_temp": None, "high": 20.0, "low": 12.0, "code": 61, "precip_prob": 60, "precip_sum": 5.0, "wind_ms": 0.4}
    t = weather.describe(place(), base, "ru")
    assert "Вероятны осадки, вероятность шестьдесят процентов." in t and "Ветра почти нет." in t and t.count("градус") == 2 and "Сейчас" not in t
    assert "Возможны осадки" in weather.describe(place(), {**base, "precip_prob": 30}, "ru")
    assert "Осадков не ожидается" in weather.describe(place(), {**base, "precip_prob": 0, "precip_sum": 0}, "ru")
    assert "Вероятны" in weather.describe(place(), {**base, "precip_prob": None, "precip_sum": 2.0}, "ru")     # вероятности нет — смотрим на сумму


# ---------------------------------------------------------------- навык и настройка
def test_home_city_setting_validation_and_roundtrip(client, auth):
    pid = client.post("/api/v1/devices", json={}, headers=auth).json()["public_id"]
    assert client.get(f"/api/v1/devices/{pid}/settings", headers=auth).json()["home_city"] == ""
    r = client.patch(f"/api/v1/devices/{pid}/settings", json={"home_city": "  Нижний   Новгород "}, headers=auth)
    assert r.status_code == 200 and r.json()["home_city"] == "Нижний Новгород" and r.json()["revision"] == 2
    for bad in ("<script>", "123", "a" * 81, "x;y"):
        assert client.patch(f"/api/v1/devices/{pid}/settings", json={"home_city": bad}, headers=auth).status_code == 422, bad
    assert client.patch(f"/api/v1/devices/{pid}/settings", json={"home_city": ""}, headers=auth).json()["home_city"] == ""     # очистка


def run_skill(client, auth, text, home="", tz="Europe/Moscow", lang="ru"):
    from noctis_server.db import SessionLocal
    from noctis_server.models import Device
    if not client.get("/api/v1/devices", headers=auth).json():
        client.post("/api/v1/devices", json={}, headers=auth)

    async def go():
        db = SessionLocal()
        try:
            device = db.query(Device).first()
            device.settings.home_city, device.settings.timezone, device.settings.language = home, tz, lang
            db.commit()
            return await run(parse(text, lang), Context(db=db, device=device, settings={"language": lang}))
        finally:
            db.close()
    return asyncio.run(go())


def test_skill_named_place_beats_home_city(client, auth, monkeypatch):
    serve(monkeypatch, geo={"москва": GEO_MOSCOW})
    assert run_skill(client, auth, "какая погода в Москве", home="Казань").text.startswith("Сегодня в Москве пасмурно.")


def test_skill_uses_home_city_then_timezone(client, auth, monkeypatch):
    kazan = {"results": [{**GEO_MOSCOW["results"][0], "name": "Казань", "latitude": 55.79}]}
    serve(monkeypatch, geo={"казань": kazan, "Moscow": GEO_MOSCOW})
    assert run_skill(client, auth, "какая погода", home="Казани").text.startswith("Сегодня в Казани")
    weather._cache.clear()
    assert run_skill(client, auth, "какая погода", home="", tz="Europe/Moscow").text.startswith("Сегодня в Москве")
    weather._cache.clear()
    # домашний город не находится — запасной вариант по поясу
    assert run_skill(client, auth, "какая погода", home="Кукуево", tz="Europe/Moscow").text.startswith("Сегодня в Москве")


def test_skill_errors_are_spoken(client, auth, monkeypatch):
    serve(monkeypatch, geo={})
    assert run_skill(client, auth, "какая погода в абракадабре").text == "Не удалось найти такое место."
    assert "домашний город" in run_skill(client, auth, "какая погода", home="", tz="UTC").text
    monkeypatch.setattr(weather, "transport", httpx.MockTransport(lambda r: httpx.Response(503)))
    assert run_skill(client, auth, "какая погода в лондоне").text.startswith("Сейчас не получается узнать погоду")
    assert run_skill(client, auth, "what is the weather in london", lang="en").text.startswith("I can't get the weather")


def test_skill_tomorrow_warns_it_only_knows_today(client, auth, monkeypatch):
    serve(monkeypatch, geo={"москва": GEO_MOSCOW})
    assert run_skill(client, auth, "погода завтра в Москве").text.startswith("Я пока знаю прогноз только на сегодня. Сегодня в Москве")


def test_forecast_is_cached(monkeypatch):
    calls = []
    serve(monkeypatch, geo={"москва": GEO_MOSCOW}, calls=calls)
    p = weather.find_place("москва", "ru")
    weather.forecast(p)
    n = len(calls)
    weather.forecast(p)
    assert len(calls) == n


def test_voice_command_end_to_end(client, auth, assistant, monkeypatch):
    """Устройство говорит «какая погода в Москве»: сервер отвечает прогнозом голосом, LLM не вызывается."""
    from .helpers import open_ws, paired, reply_text, say
    serve(monkeypatch, geo={"москва": GEO_MOSCOW})
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        text = reply_text(say(ws, assistant, "Какая погода в Москве"))
        assert text.startswith("Сегодня в Москве пасмурно. Сейчас шесть градусов")
        assert not [c for c in assistant.calls if c[0] == "llm"]
    finally:
        ctx.__exit__(None, None, None)
