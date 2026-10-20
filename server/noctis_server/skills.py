"""Выполнение голосовых команд (intents.py): время, таймеры, будильники, музыка, громкость, погода.

`run(intent, ctx)` возвращает Reply(text, action) — что сказать и (необязательно) что сделать *после* ответа
(например, запустить музыку, чтобы она не накладывалась на голос) — либо None: тогда запрос уходит в LLM
(так, «стоп» без музыки и звонка — обычная реплика). Все ответы — на языке устройства, числа словами.
"""
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import registry, scheduler as sch, weather
from .intents import Intent
from .media import jamendo, library, radio
from .media.player import MusicSession, Track
from .models import Device, DeviceSettings, UserDevice, utcnow
from .numwords import duration_words, to_words
from .personalization import normalize
from .timeutil import (WEEKDAYS, alarm_time_words, date_phrase, local_now, time_phrase,
                       weekday_list_words)

log = logging.getLogger("noctis.skills")

MAX_QUEUE = 50
VOLUME_STEP = 10


@dataclass
class Reply:
    text: str
    action: object = None            # async callable без аргументов, выполняется после озвучивания ответа


@dataclass
class Context:
    db: Session
    device: Device
    settings: dict                   # настройки устройства (язык, пол голоса, пояс ...)
    push_config: object = None       # async (dict) -> None
    now: object = None               # datetime UTC (в тестах фиксируется)
    lang: str = field(init=False)

    def __post_init__(self):
        self.lang = normalize(self.settings)["language"]


# ------------------------------------------------------------------ фразы
def L(ru, en, es, de, fr):
    return {"ru": ru, "en": en, "es": es, "de": de, "fr": fr}


T = {
    "ok": L("Хорошо.", "Okay.", "Vale.", "Okay.", "D'accord."),
    "timer_set": L("Таймер на {d} запущен.", "Timer set for {d}.", "Temporizador de {d} en marcha.",
                   "Timer auf {d} gestellt.", "Minuteur de {d} lancé."),
    "timer_ask": L("На сколько поставить таймер?", "For how long should I set the timer?",
                   "¿Cuánto tiempo pongo el temporizador?", "Auf wie lange soll ich den Timer stellen?",
                   "Pour combien de temps dois-je régler le minuteur ?"),
    "timer_long": L("Таймер может быть не длиннее двадцати четырёх часов.", "A timer can be at most twenty-four hours.",
                    "El temporizador puede durar como máximo veinticuatro horas.",
                    "Ein Timer kann höchstens vierundzwanzig Stunden dauern.",
                    "Un minuteur ne peut pas dépasser vingt-quatre heures."),
    "limit": L("Слишком много. Сначала удалите что-нибудь лишнее.", "That's too many. Please remove one first.",
               "Hay demasiados. Elimina alguno primero.", "Das sind zu viele. Bitte zuerst einen löschen.",
               "Il y en a trop. Supprimez-en un d'abord."),
    "timer_none": L("Активных таймеров нет.", "There are no active timers.", "No hay temporizadores activos.",
                    "Es gibt keine aktiven Timer.", "Aucun minuteur actif."),
    "timer_which": L("Таймеров несколько. Скажите «отмени все таймеры» или назовите длительность.",
                     "You have several timers. Say cancel all timers, or name the duration.",
                     "Tienes varios temporizadores. Di cancela todos, o indica la duración.",
                     "Es laufen mehrere Timer. Sagen Sie alle Timer löschen oder nennen Sie die Dauer.",
                     "Vous avez plusieurs minuteurs. Dites annule tous les minuteurs, ou donnez la durée."),
    "timer_cancelled": L("Таймер отменён.", "Timer cancelled.", "Temporizador cancelado.", "Timer gelöscht.",
                         "Minuteur annulé."),
    "timers_cancelled": L("Все таймеры отменены.", "All timers cancelled.", "Todos los temporizadores cancelados.",
                          "Alle Timer gelöscht.", "Tous les minuteurs sont annulés."),
    "timer_paused": L("Таймер на паузе, осталось {d}.", "Timer paused, {d} left.", "Temporizador en pausa, quedan {d}.",
                      "Timer pausiert, noch {d}.", "Minuteur en pause, il reste {d}."),
    "timer_resumed": L("Таймер продолжен, осталось {d}.", "Timer resumed, {d} left.",
                       "Temporizador reanudado, quedan {d}.", "Timer läuft weiter, noch {d}.",
                       "Minuteur relancé, il reste {d}."),
    "timer_left": L("Осталось {d}.", "{d} left.", "Quedan {d}.", "Noch {d}.", "Il reste {d}."),
    "timers_left": L("Таймеров: {n}. Ближайший: осталось {d}.", "{n} timers. The nearest has {d} left.",
                     "Hay {n} temporizadores. El más cercano: quedan {d}.", "Es laufen {n} Timer. Der nächste: noch {d}.",
                     "{n} minuteurs. Le plus proche : il reste {d}."),
    "timer_done": L("Таймер завершён.", "Time is up.", "El temporizador ha terminado.", "Der Timer ist abgelaufen.",
                    "Le minuteur est terminé."),
    "timer_done_label": L("Таймер «{label}» завершён.", "Timer {label} is done.", "El temporizador {label} ha terminado.",
                          "Der Timer {label} ist abgelaufen.", "Le minuteur {label} est terminé."),
    "alarm_ask": L("На какое время поставить будильник?", "What time should I set the alarm for?",
                   "¿Para qué hora pongo la alarma?", "Auf welche Uhrzeit soll ich den Wecker stellen?",
                   "Pour quelle heure dois-je régler le réveil ?"),
    "alarm_set": L("Будильник установлен на {t}{x}.", "Alarm set for {t}{x}.", "Alarma puesta a {t}{x}.",
                   "Wecker gestellt auf {t}{x}.", "Réveil réglé sur {t}{x}."),
    "alarm_none": L("Будильников нет.", "There are no alarms.", "No hay alarmas.", "Es gibt keine Wecker.",
                    "Aucun réveil."),
    "alarm_cancelled": L("Будильник отменён.", "Alarm cancelled.", "Alarma cancelada.", "Wecker gelöscht.",
                         "Réveil annulé."),
    "alarms_cancelled": L("Все будильники отменены.", "All alarms cancelled.", "Todas las alarmas canceladas.",
                          "Alle Wecker gelöscht.", "Tous les réveils sont annulés."),
    "alarm_which": L("Какой будильник отменить? Назовите время или скажите «все».",
                     "Which alarm? Name the time or say all.", "¿Qué alarma? Di la hora o di todas.",
                     "Welchen Wecker? Nennen Sie die Uhrzeit oder sagen Sie alle.",
                     "Quel réveil ? Dites l'heure ou dites tous."),
    "alarm_stopped": L("Выключаю.", "Stopped.", "Apagada.", "Ausgeschaltet.", "C'est éteint."),
    "alarm_list": L("Будильников: {n}. {items}", "You have {n} alarms. {items}", "Tienes {n} alarmas. {items}",
                    "Sie haben {n} Wecker. {items}", "Vous avez {n} réveils. {items}"),
    "alarm_ring": L("Пора вставать! Сейчас {t}.", "Wake up! It's {t}.", "¡Despierta! Son {t}.", "Aufwachen! Es ist {t}.",
                    "Réveillez-vous ! Il est {t}."),
    "alarm_ring_label": L("Будильник «{label}». Сейчас {t}.", "Alarm {label}. It's {t}.", "Alarma {label}. Son {t}.",
                          "Wecker {label}. Es ist {t}.", "Réveil {label}. Il est {t}."),
    "playing": L("Включаю {what}.", "Playing {what}.", "Reproduzco {what}.", "Ich spiele {what}.", "Je lance {what}."),
    "playing_music": L("Включаю музыку.", "Playing music.", "Pongo música.", "Ich spiele Musik.", "Je lance la musique."),
    "radio_playing": L("Включаю радио {what}.", "Playing the radio station {what}.", "Pongo la radio {what}.",
                       "Ich spiele das Radio {what}.", "Je lance la radio {what}."),
    "no_music": L("Не нашёл такой музыки в вашей библиотеке.", "I couldn't find that in your library.",
                  "No he encontrado eso en tu biblioteca.", "Das habe ich in Ihrer Bibliothek nicht gefunden.",
                  "Je n'ai pas trouvé cela dans votre bibliothèque."),
    "empty_library": L("В вашей библиотеке пока нет музыки. Загрузите треки в приложении или попросите включить радио.",
                       "Your library is empty. Upload tracks in the app, or ask me to play the radio.",
                       "Tu biblioteca está vacía. Sube canciones en la aplicación o pídeme la radio.",
                       "Ihre Bibliothek ist leer. Laden Sie Titel in der App hoch oder bitten Sie mich um Radio.",
                       "Votre bibliothèque est vide. Ajoutez des morceaux dans l'application ou demandez la radio."),
    "no_station": L("Не нашёл такую радиостанцию.", "I couldn't find that radio station.",
                    "No he encontrado esa emisora.", "Diesen Radiosender habe ich nicht gefunden.",
                    "Je n'ai pas trouvé cette station de radio."),
    "no_stations": L("Радиостанции ещё не добавлены. Добавьте их в приложении.",
                     "No radio stations yet. Add some in the app.", "Aún no hay emisoras. Añádelas en la aplicación.",
                     "Es sind noch keine Radiosender gespeichert. Fügen Sie sie in der App hinzu.",
                     "Aucune station de radio. Ajoutez-en dans l'application."),
    "jamendo_off": L("Джамендо не настроен на сервере.", "Jamendo is not set up on the server.",
                     "Jamendo no está configurado en el servidor.", "Jamendo ist auf dem Server nicht eingerichtet.",
                     "Jamendo n'est pas configuré sur le serveur."),
    "jamendo_none": L("В Джамендо ничего не нашлось.", "Nothing found on Jamendo.", "No he encontrado nada en Jamendo.",
                      "Auf Jamendo wurde nichts gefunden.", "Rien trouvé sur Jamendo."),
    "jamendo_key": L("Джамендо не принял ключ доступа. Проверьте client_id на сервере.", "Jamendo rejected the access key. Check the client_id on the server.",
                     "Jamendo no aceptó la clave de acceso. Revisa el client_id en el servidor.",
                     "Jamendo hat den Zugangsschlüssel abgelehnt. Prüfen Sie die client_id auf dem Server.",
                     "Jamendo a refusé la clé d’accès. Vérifiez le client_id sur le serveur."),
    "jamendo_error": L("Джамендо сейчас недоступен.", "Jamendo is unavailable right now.",
                       "Jamendo no está disponible ahora.", "Jamendo ist gerade nicht erreichbar.",
                       "Jamendo est indisponible pour le moment."),
    "play_error": L("Не получилось включить: источник недоступен.", "I couldn't play that: the source is unavailable.",
                    "No he podido reproducirlo: la fuente no está disponible.",
                    "Das konnte ich nicht abspielen: Die Quelle ist nicht erreichbar.",
                    "Je n'ai pas pu le lire : la source est indisponible."),
    "volume": L("Громкость {n}.", "Volume {n}.", "Volumen {n}.", "Lautstärke {n}.", "Volume {n}."),
    "weather_no_place": L("Не знаю, где вы находитесь. Назовите город, например: какая погода в Казани, или укажите домашний город в приложении.",
                          "I don't know where you are. Name a city, for example: what's the weather in London, or set a home city in the app.",
                          "No sé dónde estás. Nombra una ciudad, por ejemplo: qué tiempo hace en Madrid, o indica una ciudad de casa en la aplicación.",
                          "Ich weiß nicht, wo Sie sind. Nennen Sie eine Stadt, zum Beispiel: Wetter in Berlin, oder legen Sie in der App einen Heimatort fest.",
                          "Je ne sais pas où vous êtes. Dites une ville, par exemple : la météo à Paris, ou indiquez une ville dans l'application."),
    "weather_not_found": L("Не удалось найти такое место.", "I couldn't find that place.", "No he encontrado ese lugar.",
                           "Diesen Ort habe ich nicht gefunden.", "Je n'ai pas trouvé ce lieu."),
    "weather_error": L("Сейчас не получается узнать погоду. Попробуйте позже.", "I can't get the weather right now. Please try again later.",
                       "Ahora no puedo consultar el tiempo. Inténtalo más tarde.", "Ich kann das Wetter gerade nicht abrufen. Bitte versuchen Sie es später.",
                       "Je ne peux pas obtenir la météo pour le moment. Réessayez plus tard."),
    "weather_today_only": L("Я пока знаю прогноз только на сегодня.", "I only know today's forecast for now.",
                            "Por ahora solo conozco el pronóstico de hoy.", "Ich kenne vorerst nur die Vorhersage für heute.",
                            "Pour l'instant, je ne connais que les prévisions d'aujourd'hui."),
}


def tr(key: str, lang: str, **kw) -> str:
    return T[key][lang].format(**kw)


# ------------------------------------------------------------------ вспомогательное
def owner_id(db: Session, device: Device) -> int | None:
    return db.scalar(select(UserDevice.user_id).where(UserDevice.device_id == device.id)
                     .order_by(UserDevice.is_owner.desc(), UserDevice.id))


def get_session(public_id: str) -> MusicSession:
    session = registry.players.get(public_id)
    if session is None:
        session = registry.players[public_id] = MusicSession(public_id)
    return session


def _day_word(offset: int, lang: str) -> str:
    return {0: L("сегодня", "today", "hoy", "heute", "aujourd'hui"), 1: L("завтра", "tomorrow", "mañana", "morgen", "demain"),
            2: L("послезавтра", "the day after tomorrow", "pasado mañana", "übermorgen", "après-demain")}[offset][lang]


# ------------------------------------------------------------------ таймеры
def _timers_for(ctx: Context, seconds: int = 0):
    timers = sch.active_timers(ctx.db, ctx.device)
    if seconds:
        exact = [t for t in timers if t.duration_sec == seconds]
        return exact or timers
    return timers


async def _timer_set(it: Intent, ctx: Context) -> Reply:
    secs = it.params.get("seconds", 0)
    if not secs:
        return Reply(tr("timer_ask", ctx.lang))
    try:
        timer = sch.create_timer(ctx.db, ctx.device, secs, now=ctx.now)
    except sch.ScheduleError as e:
        return Reply(tr("timer_long" if str(e) == "duration" else "limit", ctx.lang))
    await sch.push_schedule_for(ctx.device.public_id)
    return Reply(tr("timer_set", ctx.lang, d=duration_words(timer.duration_sec, ctx.lang)))


async def _timer_cancel(it: Intent, ctx: Context) -> Reply | None:
    pid = ctx.device.public_id
    if sch.is_ringing(pid) and not it.params.get("seconds"):
        sch.stop_ringing(pid)
        await _send_stop_ring(pid)
        return Reply(tr("alarm_stopped", ctx.lang))
    timers = _timers_for(ctx, it.params.get("seconds", 0))
    if not timers:
        if sch.stop_ringing(pid):
            await _send_stop_ring(pid)
            return Reply(tr("alarm_stopped", ctx.lang))
        return Reply(tr("timer_none", ctx.lang))
    if it.params.get("all"):
        for t in timers:
            sch.cancel_timer(ctx.db, t)
        text = tr("timers_cancelled", ctx.lang)
    elif len(timers) == 1 or (it.params.get("seconds") and len({t.duration_sec for t in timers}) == 1 and
                              timers[0].duration_sec == it.params["seconds"]):
        sch.cancel_timer(ctx.db, timers[0])
        text = tr("timer_cancelled", ctx.lang)
    else:
        return Reply(tr("timer_which", ctx.lang))
    await sch.push_schedule_for(pid)
    return Reply(text)


async def _timer_pause_resume(it: Intent, ctx: Context) -> Reply:
    pausing = it.kind == "timer_pause"
    timers = [t for t in sch.active_timers(ctx.db, ctx.device) if t.state == ("running" if pausing else "paused")]
    if not timers:
        return Reply(tr("timer_none", ctx.lang))
    timer = min(timers, key=lambda t: sch.remaining_sec(t, ctx.now))
    (sch.pause_timer if pausing else sch.resume_timer)(ctx.db, timer, ctx.now)
    await sch.push_schedule_for(ctx.device.public_id)
    return Reply(tr("timer_paused" if pausing else "timer_resumed", ctx.lang,
                    d=duration_words(sch.remaining_sec(timer, ctx.now), ctx.lang)))


async def _timer_query(it: Intent, ctx: Context) -> Reply:
    timers = sch.active_timers(ctx.db, ctx.device)
    if not timers:
        return Reply(tr("timer_none", ctx.lang))
    nearest = min(timers, key=lambda t: sch.remaining_sec(t, ctx.now))
    d = duration_words(sch.remaining_sec(nearest, ctx.now), ctx.lang)
    if len(timers) == 1:
        return Reply(tr("timer_left", ctx.lang, d=d))
    return Reply(tr("timers_left", ctx.lang, n=to_words(len(timers), ctx.lang), d=d))


# ------------------------------------------------------------------ будильники
def _alarm_extra(alarm_days: list[int], repeat: bool, offset: int, lang: str) -> str:
    if alarm_days and repeat:
        return ", " + weekday_list_words(alarm_days, lang)
    if alarm_days:
        return ", " + WEEKDAYS[lang][alarm_days[0]]
    return ", " + _day_word(offset, lang) if offset else ""


async def _alarm_set(it: Intent, ctx: Context) -> Reply:
    p, tz = it.params, ctx.device.settings.timezone
    days, repeat, offset = p.get("weekdays") or [], p.get("repeat", False), p.get("day_offset", 0)
    try:
        if p.get("relative_seconds"):
            target = local_now(tz, (ctx.now or utcnow()) + timedelta(seconds=p["relative_seconds"]))
            if p["relative_seconds"] < 60 and target.minute == local_now(tz, ctx.now).minute:
                target += timedelta(minutes=1)
            alarm = sch.create_alarm(ctx.db, ctx.device, target.hour, target.minute, [], now=ctx.now,
                                     one_shot_date=target.date().isoformat())
            days, repeat, offset = [], False, 0
        elif p.get("clock"):
            c = p["clock"]
            hours = [c["hour"]]
            if not c["known"] and 1 <= c["hour"] <= 12 and not (days and repeat):
                hours = [c["hour"] % 12, c["hour"] % 12 + 12]     # «в 7» — ближайшие 7:00 или 19:00
            # повторяющийся будильник без «утра/вечера» — как сказано (7:30 — это утро)
            best = min(hours, key=lambda h: sch.next_occurrence(h, c["minute"], days, tz, ctx.now, offset))
            if days and not repeat:       # «в понедельник в 7» — один раз, в ближайший понедельник
                when = sch.next_occurrence(best, c["minute"], days, tz, ctx.now, offset)
                alarm = sch.create_alarm(ctx.db, ctx.device, best, c["minute"], [], now=ctx.now,
                                         one_shot_date=when.date().isoformat())
            else:
                alarm = sch.create_alarm(ctx.db, ctx.device, best, c["minute"], days if repeat else [],
                                         day_offset=offset, now=ctx.now)
        else:
            return Reply(tr("alarm_ask", ctx.lang))
    except sch.ScheduleError as e:
        return Reply(tr("limit" if str(e) == "limit" else "alarm_ask", ctx.lang))
    await sch.push_schedule_for(ctx.device.public_id)
    extra = _alarm_extra(days or alarm.weekdays, repeat, offset if not days else 0, ctx.lang)
    if not alarm.weekdays and not offset and not days:
        # день не назван: уточняем «сегодня/завтра» по реальной дате срабатывания
        today = local_now(tz, ctx.now).date().isoformat()
        extra = ", " + _day_word(0 if alarm.one_shot_date == today else 1, ctx.lang)
    return Reply(tr("alarm_set", ctx.lang, t=alarm_time_words(alarm.hour, alarm.minute, ctx.lang), x=extra))


async def _alarm_cancel(it: Intent, ctx: Context) -> Reply:
    pid = ctx.device.public_id
    if sch.is_ringing(pid):
        sch.stop_ringing(pid)
        await _send_stop_ring(pid)
        return Reply(tr("alarm_stopped", ctx.lang))
    alarms = sch.alarm_list(ctx.db, ctx.device)
    if not alarms:
        return Reply(tr("alarm_none", ctx.lang))
    clock = it.params.get("clock")
    if clock:
        hours = {clock["hour"]} if clock["known"] else {clock["hour"] % 12, clock["hour"] % 12 + 12}
        chosen = [a for a in alarms if a.hour in hours and a.minute == clock["minute"]]
        if not chosen:
            return Reply(tr("alarm_none", ctx.lang))
    elif it.params.get("all"):
        chosen = alarms
    elif len(alarms) == 1:
        chosen = alarms
    else:
        return Reply(tr("alarm_which", ctx.lang))
    for a in chosen:
        ctx.db.delete(a)
    ctx.db.commit()
    await sch.push_schedule_for(pid)
    return Reply(tr("alarms_cancelled" if len(chosen) > 1 else "alarm_cancelled", ctx.lang))


async def _alarm_list(it: Intent, ctx: Context) -> Reply:
    alarms = [a for a in sch.alarm_list(ctx.db, ctx.device) if a.enabled]
    if not alarms:
        return Reply(tr("alarm_none", ctx.lang))
    items = []
    for a in alarms[:5]:
        s = alarm_time_words(a.hour, a.minute, ctx.lang)
        if a.weekdays:
            s += ", " + weekday_list_words(a.weekdays, ctx.lang)
        items.append(s)
    return Reply(tr("alarm_list", ctx.lang, n=to_words(len(alarms), ctx.lang), items="; ".join(items) + "."))


async def _send_stop_ring(public_id: str) -> None:
    conn = registry.online_devices.get(public_id)
    if conn:
        await conn.send_json({"type": "command", "name": "stop_ring"})


# ------------------------------------------------------------------ музыка
def _tracks(rows) -> list[Track]:
    out = []
    for t in rows[:MAX_QUEUE]:
        try:
            uri = library.audio_uri(t)
        except library.LibraryError:
            continue
        out.append(Track(t.title, t.artist, t.source, uri, t.id))
    return out


async def _music_play(it: Intent, ctx: Context) -> Reply:
    lang, query, source = ctx.lang, it.params.get("query", ""), it.params.get("source")
    uid = owner_id(ctx.db, ctx.device)
    pid = ctx.device.public_id
    tracks: list[Track] = []
    say = tr("playing_music", lang)

    if source == "radio":
        stations = radio.find_station(ctx.db, uid, query) if uid else []
        if not stations:
            return Reply(tr("no_station" if radio.list_stations(ctx.db, uid) else "no_stations", lang))
        tracks = _tracks(stations[:1])
        say = tr("radio_playing", lang, what=tracks[0].title)
    else:
        if source in (None, "local") and uid:
            found = library.search(ctx.db, uid, query)
            if found and not query:
                random.shuffle(found)
            tracks = _tracks(found)
            if tracks and query:
                say = tr("playing", lang, what=tracks[0].label())
        if not tracks and (source == "jamendo" or (source is None and jamendo.configured())):
            if not jamendo.configured():
                return Reply(tr("jamendo_off", lang))
            try:
                results = await _jamendo_search(query)
            except jamendo.JamendoKeyError:
                return Reply(tr("jamendo_key", lang))
            except jamendo.JamendoError:
                return Reply(tr("jamendo_error", lang))
            if not results:
                return Reply(tr("jamendo_none", lang))
            tracks = _tracks(jamendo.to_tracks(ctx.db, uid, results)) if uid else []
            say = tr("playing", lang, what=tracks[0].label()) if query and tracks else tr("playing_music", lang)
        if not tracks:
            have_any = bool(uid and library.search(ctx.db, uid, ""))
            return Reply(tr("no_music" if have_any or source == "local" and query else "empty_library", lang))

    async def start():
        session = get_session(pid)
        if not await session.play(tracks):
            conn = registry.online_devices.get(pid)
            if conn and session.last_error != "offline":
                log.warning("[%s] музыка не запустилась: %s", pid, session.last_error)

    return Reply(say, start)


async def _weather(it: Intent, ctx: Context) -> Reply:
    """Погода на сегодня: место из фразы, иначе домашний город из настроек, иначе город часового пояса устройства."""
    import asyncio
    lang = ctx.lang
    named = (it.params.get("place") or "").strip()
    home = (getattr(ctx.device.settings, "home_city", "") or "").strip()
    try:
        place = None
        if named:
            place = await asyncio.to_thread(weather.find_place, named, lang)
        else:
            if home:
                try:
                    place = await asyncio.to_thread(weather.find_place, home, lang)
                except weather.PlaceNotFound:
                    place = None                                    # домашний город не находится — пробуем пояс
            if place is None:
                place = await asyncio.to_thread(weather.place_from_timezone, ctx.device.settings.timezone, lang)
            if place is None:
                return Reply(tr("weather_no_place", lang))
        data = await asyncio.to_thread(weather.forecast, place)
    except weather.PlaceNotFound:
        return Reply(tr("weather_not_found", lang))
    except weather.WeatherError as e:
        log.warning("погода недоступна: %s", e)
        return Reply(tr("weather_error", lang))
    text = weather.describe(place, data, lang)
    if it.params.get("day"):
        text = tr("weather_today_only", lang) + " " + text
    return Reply(text)


async def _jamendo_search(query: str):
    import asyncio
    return await asyncio.to_thread(jamendo.search, query, 10)


async def _music_control(it: Intent, ctx: Context) -> Reply | None:
    session = registry.players.get(ctx.device.public_id)
    active = session is not None and session.state != "stopped"
    ok = Reply(tr("ok", ctx.lang))
    if it.kind == "music_pause":
        if active:
            return Reply(ok.text, session.pause)
    elif it.kind == "music_resume":
        if active and session.state == "paused":
            return Reply(ok.text, session.resume)
    elif it.kind == "music_next":
        if active:
            return Reply(ok.text, session.next)
    elif it.kind == "music_prev":
        if active:
            return Reply(ok.text, session.prev)
    return None            # музыки нет — это обычная реплика, пусть отвечает LLM


async def _stop(it: Intent, ctx: Context) -> Reply | None:
    pid = ctx.device.public_id
    if sch.is_ringing(pid):
        sch.stop_ringing(pid)
        await _send_stop_ring(pid)
        return Reply(tr("alarm_stopped", ctx.lang))
    session = registry.players.get(pid)
    if session is not None and session.state != "stopped":
        return Reply(tr("ok", ctx.lang), session.stop)
    return None


# ------------------------------------------------------------------ громкость
async def _volume(it: Intent, ctx: Context) -> Reply:
    s: DeviceSettings = ctx.device.settings
    if it.kind == "volume_set":
        value = it.params["value"]
    else:
        value = s.volume + (VOLUME_STEP if it.kind == "volume_up" else -VOLUME_STEP)
    value = max(0, min(100, value))
    if value != s.volume:
        s.volume = value
        s.revision += 1
        ctx.db.commit()
        if ctx.push_config:
            from .services import config_payload
            await ctx.push_config(config_payload(ctx.db, s))
    return Reply(tr("volume", ctx.lang, n=to_words(value, ctx.lang)))


# ------------------------------------------------------------------ точка входа
async def run(intent: Intent, ctx: Context) -> Reply | None:
    k = intent.kind
    if k == "time":
        return Reply(time_phrase(local_now(ctx.device.settings.timezone, ctx.now), ctx.lang))
    if k == "date":
        return Reply(date_phrase(local_now(ctx.device.settings.timezone, ctx.now), ctx.lang))
    if k == "timer_set":
        return await _timer_set(intent, ctx)
    if k == "timer_cancel":
        return await _timer_cancel(intent, ctx)
    if k in ("timer_pause", "timer_resume"):
        return await _timer_pause_resume(intent, ctx)
    if k == "timer_query":
        return await _timer_query(intent, ctx)
    if k == "alarm_set":
        return await _alarm_set(intent, ctx)
    if k == "alarm_cancel":
        return await _alarm_cancel(intent, ctx)
    if k == "alarm_list":
        return await _alarm_list(intent, ctx)
    if k == "music_play":
        return await _music_play(intent, ctx)
    if k in ("music_pause", "music_resume", "music_next", "music_prev"):
        return await _music_control(intent, ctx)
    if k == "weather":
        return await _weather(intent, ctx)
    if k == "stop":
        return await _stop(intent, ctx)
    if k in ("volume_set", "volume_up", "volume_down"):
        return await _volume(intent, ctx)
    return None


def fired_phrase(fired: sch.Fired, now=None) -> str:
    """Голосовое уведомление о срабатывании на языке устройства."""
    lang = normalize(fired.settings)["language"]
    if fired.kind == "timer":
        return tr("timer_done_label", lang, label=fired.label) if fired.label else tr("timer_done", lang)
    t = alarm_time_words(fired.hour, fired.minute, lang)
    return tr("alarm_ring_label", lang, label=fired.label, t=t) if fired.label else tr("alarm_ring", lang, t=t)
