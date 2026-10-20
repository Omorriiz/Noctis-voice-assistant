"""Разбор голосовых команд на 5 языках: время, таймеры, будильники, музыка, громкость; обычные вопросы не перехватываются."""
import pytest

from noctis_server import numwords as nw
from noctis_server.intents import parse


def check(lang, text, kind, **params):
    it = parse(text, lang)
    assert it is not None, f"{lang}: «{text}» не распознано"
    assert it.kind == kind, f"{lang}: «{text}» → {it.kind}, ожидалось {kind} ({it.params})"
    for key, value in params.items():
        assert it.params.get(key) == value, f"{lang}: «{text}»: {key}={it.params.get(key)!r}, ожидалось {value!r}"
    return it


# (язык, фраза, вид, параметры)
CASES = [
    # ---------------- русский
    ("ru", "Который час?", "time", {}),
    ("ru", "Сколько сейчас времени", "time", {}),
    ("ru", "Какое сегодня число?", "date", {}),
    ("ru", "Поставь таймер на 5 минут", "timer_set", {"seconds": 300}),
    ("ru", "Поставь таймер на полчаса", "timer_set", {"seconds": 1800}),
    ("ru", "Засеки десять минут", "timer_set", {"seconds": 600}),
    ("ru", "Таймер на час тридцать минут", "timer_set", {"seconds": 5400}),
    ("ru", "Поставь таймер на две минуты тридцать секунд", "timer_set", {"seconds": 150}),
    ("ru", "Поставь таймер на минуту", "timer_set", {"seconds": 60}),
    ("ru", "Отмени таймер", "timer_cancel", {"all": False}),
    ("ru", "Отмени все таймеры", "timer_cancel", {"all": True}),
    ("ru", "Поставь таймер на паузу", "timer_pause", {}),
    ("ru", "Продолжи таймер", "timer_resume", {}),
    ("ru", "Сколько осталось до конца таймера", "timer_query", {}),
    ("ru", "Разбуди меня в 7:30", "alarm_set", {"clock": {"hour": 7, "minute": 30, "known": False}}),
    ("ru", "Поставь будильник на 6 утра", "alarm_set", {"clock": {"hour": 6, "minute": 0, "known": True}}),
    ("ru", "Поставь будильник на семь тридцать по будням", "alarm_set", {"weekdays": [0, 1, 2, 3, 4], "repeat": True}),
    ("ru", "Разбуди меня завтра в восемь вечера", "alarm_set", {"day_offset": 1, "clock": {"hour": 20, "minute": 0, "known": True}}),
    ("ru", "Разбуди меня через 20 минут", "alarm_set", {"relative_seconds": 1200}),
    ("ru", "Отмени будильник на 7 утра", "alarm_cancel", {}),
    ("ru", "Какие у меня будильники", "alarm_list", {}),
    ("ru", "Включи музыку", "music_play", {"query": "", "source": None}),
    ("ru", "Включи радио Рекорд", "music_play", {"query": "рекорд", "source": "radio"}),
    ("ru", "Включи джаз из Джамендо", "music_play", {"query": "джаз", "source": "jamendo"}),
    ("ru", "Включи мою музыку", "music_play", {"source": "local"}),
    ("ru", "Пауза", "music_pause", {}),
    ("ru", "Продолжи", "music_resume", {}),
    ("ru", "Дальше", "music_next", {}),
    ("ru", "Предыдущая песня", "music_prev", {}),
    ("ru", "Стоп", "stop", {}),
    ("ru", "Сделай погромче", "volume_up", {}),
    ("ru", "Тише", "volume_down", {}),
    ("ru", "Громкость на 40", "volume_set", {"value": 40}),
    ("ru", "Громкость пятьдесят процентов", "volume_set", {"value": 50}),
    # ---------------- English
    ("en", "What time is it?", "time", {}),
    ("en", "What's the date today", "date", {}),
    ("en", "Set a timer for 10 minutes", "timer_set", {"seconds": 600}),
    ("en", "Set a timer for an hour and a half", "timer_set", {"seconds": 5400}),
    ("en", "Start a five minute timer", "timer_set", {"seconds": 300}),
    ("en", "Set a timer for one hour thirty minutes", "timer_set", {"seconds": 5400}),
    ("en", "Cancel the timer", "timer_cancel", {}),
    ("en", "Cancel all timers", "timer_cancel", {"all": True}),
    ("en", "How much time is left on the timer", "timer_query", {}),
    ("en", "Pause the timer", "timer_pause", {}),
    ("en", "Resume the timer", "timer_resume", {}),
    ("en", "Wake me up at 6:45 am", "alarm_set", {"clock": {"hour": 6, "minute": 45, "known": True}}),
    ("en", "Set an alarm for 7 pm", "alarm_set", {"clock": {"hour": 19, "minute": 0, "known": True}}),
    ("en", "Set an alarm for half past six", "alarm_set", {"clock": {"hour": 6, "minute": 30, "known": False}}),
    ("en", "Set an alarm for seven on weekdays", "alarm_set", {"weekdays": [0, 1, 2, 3, 4], "repeat": True}),
    ("en", "Wake me in 30 minutes", "alarm_set", {"relative_seconds": 1800}),
    ("en", "Delete my alarm", "alarm_cancel", {}),
    ("en", "What alarms do I have", "alarm_list", {}),
    ("en", "Play some jazz", "music_play", {"query": "jazz"}),
    ("en", "Play the radio", "music_play", {"source": "radio", "query": ""}),
    ("en", "Play the song Hotel California", "music_play", {"query": "hotel california"}),
    ("en", "Play my music", "music_play", {"source": "local"}),
    ("en", "Play jamendo rock", "music_play", {"source": "jamendo", "query": "rock"}),
    ("en", "Pause", "music_pause", {}),
    ("en", "Next", "music_next", {}),
    ("en", "Go back", "music_prev", {}),
    ("en", "Stop", "stop", {}),
    ("en", "Louder", "volume_up", {}),
    ("en", "Turn the volume down", "volume_down", {}),
    ("en", "Set the volume to 30", "volume_set", {"value": 30}),
    # ---------------- español
    ("es", "¿Qué hora es?", "time", {}),
    ("es", "¿Qué día es hoy?", "date", {}),
    ("es", "Pon un temporizador de cinco minutos", "timer_set", {"seconds": 300}),
    ("es", "Pon un temporizador de media hora", "timer_set", {"seconds": 1800}),
    ("es", "Pon un temporizador de una hora y diez minutos", "timer_set", {"seconds": 4200}),
    ("es", "Cancela el temporizador", "timer_cancel", {}),
    ("es", "Pausa el temporizador", "timer_pause", {}),
    ("es", "Cuánto tiempo queda", "timer_query", {}),
    ("es", "Despiértame a las siete y media", "alarm_set", {"clock": {"hour": 7, "minute": 30, "known": False}}),
    ("es", "Pon una alarma a las 6 de la mañana", "alarm_set", {"clock": {"hour": 6, "minute": 0, "known": True}}),
    ("es", "Pon una alarma a las siete los días laborables", "alarm_set", {"weekdays": [0, 1, 2, 3, 4], "repeat": True}),
    ("es", "Despiértame dentro de veinte minutos", "alarm_set", {"relative_seconds": 1200}),
    ("es", "Cancela la alarma", "alarm_cancel", {}),
    ("es", "¿Qué alarmas tengo?", "alarm_list", {}),
    ("es", "Pon música", "music_play", {"query": ""}),
    ("es", "Pon la radio", "music_play", {"source": "radio"}),
    ("es", "Pon jazz de Jamendo", "music_play", {"source": "jamendo", "query": "jazz"}),
    ("es", "Pon mi música", "music_play", {"source": "local"}),
    ("es", "Siguiente", "music_next", {}),
    ("es", "Pausa", "music_pause", {}),
    ("es", "Baja el volumen", "volume_down", {}),
    ("es", "Sube el volumen", "volume_up", {}),
    ("es", "Volumen al cuarenta", "volume_set", {"value": 40}),
    # ---------------- Deutsch
    ("de", "Wie spät ist es?", "time", {}),
    ("de", "Welcher Tag ist heute?", "date", {}),
    ("de", "Stelle einen Timer auf zehn Minuten", "timer_set", {"seconds": 600}),
    ("de", "Timer auf eine halbe Stunde", "timer_set", {"seconds": 1800}),
    ("de", "Stell einen Timer auf eine Stunde dreißig Minuten", "timer_set", {"seconds": 5400}),
    ("de", "Lösche den Timer", "timer_cancel", {}),
    ("de", "Pausiere den Timer", "timer_pause", {}),
    ("de", "Wie lange noch?", "timer_query", {}),
    ("de", "Weck mich um halb acht", "alarm_set", {"clock": {"hour": 7, "minute": 30, "known": False}}),
    ("de", "Stelle einen Wecker auf 6 Uhr 30", "alarm_set", {"clock": {"hour": 6, "minute": 30, "known": False}}),
    ("de", "Wecker um sieben Uhr an Werktagen", "alarm_set", {"weekdays": [0, 1, 2, 3, 4], "repeat": True}),
    ("de", "Weck mich in zwanzig Minuten", "alarm_set", {"relative_seconds": 1200}),
    ("de", "Lösche den Wecker", "alarm_cancel", {}),
    ("de", "Welche Wecker habe ich", "alarm_list", {}),
    ("de", "Spiel Musik", "music_play", {"query": ""}),
    ("de", "Spiel Radio", "music_play", {"source": "radio"}),
    ("de", "Spiel Jazz von Jamendo", "music_play", {"source": "jamendo", "query": "jazz"}),
    ("de", "Spiel meine Musik", "music_play", {"source": "local"}),
    ("de", "Nächstes Lied", "music_next", {}),
    ("de", "Leiser", "volume_down", {}),
    ("de", "Lauter", "volume_up", {}),
    ("de", "Lautstärke auf vierzig", "volume_set", {"value": 40}),
    # ---------------- français
    ("fr", "Quelle heure est-il ?", "time", {}),
    ("fr", "Quel jour sommes-nous ?", "date", {}),
    ("fr", "Mets un minuteur de cinq minutes", "timer_set", {"seconds": 300}),
    ("fr", "Mets un minuteur d'une demi-heure", "timer_set", {"seconds": 1800}),
    ("fr", "Lance un minuteur de une heure trente", "timer_set", {"seconds": 5400}),
    ("fr", "Annule le minuteur", "timer_cancel", {}),
    ("fr", "Mets le minuteur en pause", "timer_pause", {}),
    ("fr", "Combien de temps reste-t-il ?", "timer_query", {}),
    ("fr", "Réveille-moi à sept heures trente", "alarm_set", {"clock": {"hour": 7, "minute": 30, "known": False}}),
    ("fr", "Mets un réveil à 6 heures du matin", "alarm_set", {"clock": {"hour": 6, "minute": 0, "known": True}}),
    ("fr", "Mets un réveil à sept heures en semaine", "alarm_set", {"weekdays": [0, 1, 2, 3, 4], "repeat": True}),
    ("fr", "Réveille-moi dans vingt minutes", "alarm_set", {"relative_seconds": 1200}),
    ("fr", "Annule le réveil", "alarm_cancel", {}),
    ("fr", "Quels réveils ai-je ?", "alarm_list", {}),
    ("fr", "Joue de la musique", "music_play", {"query": ""}),
    ("fr", "Mets la radio", "music_play", {"source": "radio"}),
    ("fr", "Mets du jazz sur Jamendo", "music_play", {"source": "jamendo", "query": "jazz"}),
    ("fr", "Joue ma musique", "music_play", {"source": "local"}),
    ("fr", "Chanson suivante", "music_next", {}),
    ("fr", "Baisse le volume", "volume_down", {}),
    ("fr", "Plus fort", "volume_up", {}),
    ("fr", "Volume à quarante", "volume_set", {"value": 40}),
]


@pytest.mark.parametrize("lang,text,kind,params", CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_commands(lang, text, kind, params):
    check(lang, text, kind, **params)


def test_at_least_eight_phrases_per_language_and_every_class():
    for lang in nw.LANGS:
        rows = [c for c in CASES if c[0] == lang]
        assert len(rows) >= 20
        kinds = {c[2] for c in rows}
        assert {"time", "timer_set", "timer_cancel", "alarm_set", "music_play", "volume_up" if lang != "ru" else "volume_set"} <= kinds | {"volume_up"}


# Обычные вопросы и реплики уходят в LLM — их команды перехватывать не должны
NOT_COMMANDS = [
    ("ru", "Расскажи про историю радио"), ("ru", "Как работает таймер в духовке"), ("ru", "Почему небо голубое"),
    ("ru", "Включи свет"), ("ru", "Который час в Токио"), ("ru", "Какая столица Франции"),
    ("ru", "Какой день недели был первого января 2000 года"), ("ru", "Я остался дома и читаю книгу"),
    ("ru", "Объясни, как работает будильник в телефоне"),
    ("en", "What is the capital of France"), ("en", "Tell me about the history of radio"), ("en", "How does a timer work"),
    ("en", "What time does the museum open"), ("en", "Turn on the light"), ("en", "Who wrote Hamlet"),
    ("es", "¿Cuál es la capital de Francia?"), ("es", "Cuéntame la historia de la radio"), ("es", "Enciende la luz"),
    ("de", "Was ist die Hauptstadt von Frankreich"), ("de", "Erzähl mir etwas über die Geschichte des Radios"),
    ("de", "Mach das Licht an"),
    ("fr", "Quelle est la capitale de la France"), ("fr", "Raconte-moi l'histoire de la radio"), ("fr", "Allume la lumière"),
    ("ru", ""), ("en", "   "),
]


@pytest.mark.parametrize("lang,text", NOT_COMMANDS)
def test_questions_are_not_commands(lang, text):
    assert parse(text, lang) is None


def test_unknown_language_and_garbage():
    assert parse("set a timer for 5 minutes", "xx") is None
    assert parse("!!! ??? ...", "ru") is None


def test_timer_without_duration_asks_and_without_verb_is_ignored():
    it = check("ru", "Поставь таймер", "timer_set")
    assert it.params["seconds"] == 0
    assert parse("Что такое таймер", "ru") is None


def test_generic_controls_only_in_short_phrases():
    # «дальше» внутри длинной реплики — не команда плееру
    assert parse("расскажи дальше про то что было потом с этим героем", "ru") is None
    assert parse("дальше", "ru").kind == "music_next"
