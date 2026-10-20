"""Разбор голосовых команд времени/таймеров/будильников/музыки/громкости на 5 языках — без LLM.

`parse(text, lang)` возвращает Intent или None (тогда запрос уходит в LLM). Правила намеренно узкие:
команда должна содержать глагол и существительное («поставь таймер», «включи музыку»), короткие общие команды
(«стоп», «пауза», «дальше») принимаются только в коротких фразах — чтобы не перехватывать обычные вопросы.

Поддерживаемые формы описаны в docs/stage5-time-timers-music-report.md. Все шаблоны записаны для нормализованного
текста (см. numwords.normalize): нижний регистр, без знаков препинания, у es/de/fr без диакритики.
"""
import re
from dataclasses import dataclass, field

from .numwords import ALIAS_WORDS, find_numbers, normalize

SHORT_COMMAND_TOKENS = 5


@dataclass
class Intent:
    kind: str
    params: dict = field(default_factory=dict)


# ------------------------------------------------------------------ данные языков
# Ключи: time_q date_q timer alarm set cancel pause resume left alarm_list play music radio jamendo local
#        ctl_pause ctl_resume ctl_stop ctl_next ctl_prev louder quieter vol_set
#        unit_hour unit_minute unit_second halves[(regex, секунды)] cue (слова «в/на/at»), rel (через/in), repeat,
#        weekdays[7], weekdays_group {workdays, weekend, daily}, tomorrow, after_tomorrow, am pm morning afternoon evening night
LANG = {
    "ru": dict(
        time_q=r"\b(который|сколько) (сейчас )?(час|время|времени)\b|\bскажи\w* (мне )?(пожалуйста )?(точное )?время\b|\bтекущее время\b",
        date_q=r"\bкакое (сегодня )?число\b|\bкакой (сегодня )?день\b|\bкакая (сегодня )?дата\b|\bчто за день\b|\bдата сегодня\b|\bкакой сегодня день недели\b",
        timer=r"\bтаймер\w*|\bзасек\w*",
        alarm=r"\bбудильник\w*|\bразбуди\w*|\bпобудк\w*",
        set=r"\b(постав|установ|запуст|завед|включ|создай|засек|сделай|заведи|поставит|запустит)\w*",
        cancel=r"\b(отмен|удал|останов|выключ|выруб|сброс|убер|сотр|прекрат|хватит)\w*",
        pause=r"\b(пауз|приостанов)\w*",
        resume=r"\b(продолж|возобнов)\w*",
        left=r"\bсколько (еще |уже )?(осталось|времени осталось)\b|\bсколько (еще )?до (конца|звонка)\b|\bосталось\b",
        alarm_list=r"\bкакие (у меня )?будильник\w*|\bсписок будильник\w*|\bпокажи\w* будильник\w*|\bесть ли будильник\w*|\bсколько будильник\w*|\bкогда (мой )?будильник\b",
        play=r"\b(включ|постав|запуст|игра|сыгра|воспроизв|проигра|послуша|хочу слуша|давай послуша)\w*",
        music=r"\b(музык|песн|песен|трек|композици|мелоди|альбом|плейлист|радио|радиостанци|джаз|классик|релакс|что нибудь)\w*|\bпоп\b|\bрок\w{0,2}\b",
        radio=r"\bрадио\w*|\bрадиостанци\w*|\bволну\b",
        jamendo=r"\b(джамендо|жамендо|jamendo)\b",
        local=r"\b(мою|мои|моей|мой|свою|своей|свои) (собственную )?(музык|библиотек|плейлист|песн|коллекци)\w*|\bиз (моей )?(библиотек|коллекци)\w*",
        ctl_pause=r"\b(пауз|приостанов)\w*",
        ctl_resume=r"\b(продолж|возобнов)\w*|\bиграй дальше\b|\bвключи обратно\b",
        ctl_stop=r"\bстоп\b|\bостанови\w*|\bвыключи (музыку|радио|песню|воспроизведение)\b|\bхватит\b|\bтишина\b|\bвыруби (музыку|радио)\b|\bзамолчи\b",
        ctl_next=r"\bследующ\w*|\bдальше\b|\bпропусти\w*|\bдругую (песню|композицию|трек)\b|\bпереключи\b",
        ctl_prev=r"\bпредыдущ\w*|\bназад\b|\bвернись\b|\bпрошлую (песню|композицию|трек)\b",
        louder=r"\bгромче\b|\bприбавь (звук|громкость)\b|\bувеличь (громкость|звук)\b|\bповысь громкость\b|\bпогромче\b",
        quieter=r"\bтише\b|\bубавь\b|\bуменьши (громкость|звук)\b|\bпотише\b|\bпонизь громкость\b",
        vol_set=r"\bгромкост\w* (на |до |в )?",
        unit_hour=r"^(час|часа|часов|часик\w*)$", unit_minute=r"^(минут\w*|мин)$", unit_second=r"^(секунд\w*|сек)$",
        bare_unit=r"^(час|минуту|секунду)$",
        halves=[(r"\bполтора часа\b|\bполтора часу\b", 5400), (r"\bполторы минуты\b", 90), (r"\bполчаса\b|\bполовин\w* часа\b", 1800)],
        cue=r"^(в|на|к|ровно)$", rel=r"\bчерез\b", repeat=r"\bкажд\w+|\bежедневн\w*|\bпо (будням|выходным|\w+ам)\b|\bв будни\b|\bв выходные\b",
        weekdays=[r"понедельник\w*", r"вторник\w*", r"сред[уаыеой]\w*", r"четверг\w*", r"пятниц\w*", r"суббот\w*", r"воскресень\w*"],
        wd_work=r"\bбудн\w*", wd_weekend=r"\bвыходн\w*", wd_daily=r"\bежедневно\b|\bкаждый день\b|\bкаждое утро\b|\bкаждый вечер\b",
        tomorrow=r"\bзавтра\b", after_tomorrow=r"\bпослезавтра\b",
        am=r"$^", pm=r"$^", morning=r"\b(утра|утром)\b", afternoon=r"\b(дня|днем)\b", evening=r"\b(вечера|вечером)\b", night=r"\b(ночи|ночью)\b",
        all=r"\bвсе\b|\bвсё\b", stop_ring=r"\b(выключи|останови|отключи|заткни)\w* (будильник|таймер)\w*|\bстоп\b",
    ),
    "en": dict(
        time_q=r"\bwhat time is it\b|\bwhat is the time\b|\bwhat'?s the time\b|\btell me the time\b|\bcurrent time\b|\bthe time please\b|\bwhat is the current time\b",
        date_q=r"\bwhat day is it\b|\bwhat is the date\b|\bwhat'?s the date\b|\btoday'?s date\b|\bwhat date is it\b|\bwhat day of the week\b|\bwhich day is it\b|\bwhat is today\b|\bwhat'?s today\b",
        timer=r"\btimers?\b|\bcountdown\b",
        alarm=r"\balarms?\b|\bwake me\b|\bwake up call\b",
        set=r"\b(set|start|create|make|put|begin|add|schedule)\b",
        cancel=r"\b(cancel|delete|remove|stop|turn off|clear|dismiss|kill|disable)\b",
        pause=r"\b(pause|hold)\b",
        resume=r"\b(resume|continue|unpause|restart)\b",
        left=r"\bhow (much )?(time|long)( is)?( there)?( left| remaining)\b|\btime (left|remaining)\b|\bhow many (minutes|seconds|hours) (are )?left\b|\bhow long (is )?left\b|\bhow long until\b",
        alarm_list=r"\bwhat alarms\b|\blist (my )?alarms\b|\bshow (my )?alarms\b|\bdo i have (an |any )?alarms?\b|\bhow many alarms\b|\bwhen is my alarm\b|\bmy alarms\b",
        play=r"\b(play|put on|start playing|stream|listen to|i want to (hear|listen)|turn on)\b",
        music=r"\b(music|songs?|tracks?|albums?|playlists?|radio|jazz|rock|classical|pop|something|tune|tunes|lofi|chill|relaxing)\b",
        radio=r"\bradio\b|\bstation\b",
        jamendo=r"\bjamendo\b",
        local=r"\bmy (own )?(music|library|songs|playlist|collection)\b|\bfrom my (library|collection)\b",
        ctl_pause=r"\b(pause|hold on)\b",
        ctl_resume=r"\b(resume|continue|unpause|keep playing)\b",
        ctl_stop=r"\bstop\b|\bturn off the (music|radio)\b|\bthat'?s enough\b|\bbe quiet\b|\bshut up\b|\benough\b",
        ctl_next=r"\bnext\b|\bskip\b|\bforward\b",
        ctl_prev=r"\bprevious\b|\bgo back\b|\blast (song|track)\b|\bback\b",
        louder=r"\blouder\b|\bturn it up\b|\bvolume up\b|\b(increase|raise) (the )?volume\b",
        quieter=r"\bquieter\b|\bsofter\b|\bturn it down\b|\bvolume down\b|\b(decrease|lower|reduce) (the )?volume\b",
        vol_set=r"\bvolume (to|at|on)? ?|\bset (the )?volume (to|at)? ?",
        unit_hour=r"^(hour|hours|hr|hrs)$", unit_minute=r"^(minute|minutes|min|mins)$", unit_second=r"^(second|seconds|sec|secs)$",
        bare_unit=r"^$",
        halves=[(r"\ban hour and a half\b", 5400), (r"\bhalf an hour\b|\bhalf hour\b|\ba half hour\b", 1800)],
        cue=r"^(at|for|to)$", rel=r"\bin\b|\bafter\b", repeat=r"\bevery\b|\bdaily\b|\bweekdays\b|\bweekends\b|\bon (mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays)\b",
        weekdays=[r"mondays?", r"tuesdays?", r"wednesdays?", r"thursdays?", r"fridays?", r"saturdays?", r"sundays?"],
        wd_work=r"\bweekdays?\b|\bworkdays?\b|\bworking days\b", wd_weekend=r"\bweekends?\b", wd_daily=r"\bevery day\b|\bdaily\b|\beveryday\b",
        tomorrow=r"\btomorrow\b", after_tomorrow=r"\bday after tomorrow\b",
        am=r"\b(a m|am)\b", pm=r"\b(p m|pm)\b", morning=r"\bin the morning\b", afternoon=r"\bin the afternoon\b", evening=r"\bin the evening\b|\bat night\b|\btonight\b", night=r"\bat midnight\b",
        all=r"\ball\b|\bevery\b", stop_ring=r"\b(turn off|stop|dismiss|silence)\b.*\b(alarm|timer)\b|\bstop\b",
    ),
    "es": dict(
        time_q=r"\bque hora es\b|\bque hora son\b|\bdime la hora\b|\bhora actual\b|\ba que hora estamos\b|\bque hora tienes\b",
        date_q=r"\bque dia es\b|\ba que fecha\b|\bque fecha\b|\ba cuantos estamos\b|\bque dia de la semana\b|\bcual es la fecha\b",
        timer=r"\btemporizador\w*|\bcuenta atras\b|\btimer\b|\bcronometro\b",
        alarm=r"\balarma\w*|\bdespiertame\b|\bdespierta me\b",
        set=r"\b(pon|pone|programa|configura|inicia|activa|crea|establece|ajusta|poner|programar)\w*",
        cancel=r"\b(cancela|elimina|borra|apaga|quita|deten|detener|para|desactiva)\w*",
        pause=r"\bpausa\w*|\bpausar\b",
        resume=r"\b(reanuda|continua|sigue|reanudar)\w*",
        left=r"\bcuanto (tiempo )?(queda|falta)\b|\btiempo restante\b|\bcuanto me queda\b",
        alarm_list=r"\bque alarmas\b|\blista de alarmas\b|\bmuestra\w* (mis )?alarmas\b|\btengo alguna alarma\b|\bcuantas alarmas\b|\bmis alarmas\b|\bcuando suena mi alarma\b",
        play=r"\b(pon|pone|reproduce|toca|escuchar|quiero escuchar|poner|reproducir)\w*",
        music=r"\b(musica|cancion|canciones|tema|temas|disco|album|playlist|radio|jazz|rock|clasica|pop|algo)\w*|\blista de reproduccion\b",
        radio=r"\bradio\w*|\bemisora\w*",
        jamendo=r"\bjamendo\b",
        local=r"\bmi (propia )?(musica|biblioteca|coleccion|lista)\b|\bmis (canciones|temas)\b|\bde mi (biblioteca|coleccion)\b",
        ctl_pause=r"\bpausa\w*|\bpausar\b",
        ctl_resume=r"\b(reanuda|continua|sigue)\w*",
        ctl_stop=r"\bstop\b|\bpara\b|\bdetente\b|\bdeten\w*|\bapaga (la )?(musica|radio)\b|\bbasta\b|\bsilencio\b",
        ctl_next=r"\bsiguiente\w*|\bsalta\w*|\bpasa\b|\bproxim\w*",
        ctl_prev=r"\banterior\w*|\batras\b|\bvuelve\b",
        louder=r"\bsube\w* (el )?volumen\b|\bmas alto\b|\bmas fuerte\b|\bsube\b",
        quieter=r"\bbaja\w* (el )?volumen\b|\bmas bajo\b|\bmas suave\b|\bbaja\b",
        vol_set=r"\bvolumen (al|a|en)? ?",
        unit_hour=r"^horas?$", unit_minute=r"^minutos?$", unit_second=r"^segundos?$",
        bare_unit=r"^$",
        halves=[(r"\bhora y media\b", 5400), (r"\bmedia hora\b", 1800)],
        cue=r"^(a|para|las|la|a las|a la|sobre)$", rel=r"\bdentro de\b|\ben\b", repeat=r"\bcada\b|\btodos los\b|\blos (lunes|martes|miercoles|jueves|viernes|sabados|domingos)\b|\bdias laborables\b|\bfines de semana\b",
        weekdays=[r"lunes", r"martes", r"miercoles", r"jueves", r"viernes", r"sabados?", r"domingos?"],
        wd_work=r"\bdias laborables\b|\bde lunes a viernes\b|\blaborables\b", wd_weekend=r"\bfines? de semana\b", wd_daily=r"\btodos los dias\b|\bcada dia\b|\bdiariamente\b",
        tomorrow=r"(?<!la )\bmanana\b", after_tomorrow=r"\bpasado manana\b",
        am=r"$^", pm=r"$^", morning=r"\b(de|por|en) la manana\b", afternoon=r"\b(de|por|en) la tarde\b", evening=r"\b(de|por|en) la noche\b", night=r"\bde la madrugada\b",
        all=r"\btodas?\b|\btodos\b", stop_ring=r"\b(apaga|deten|para)\w* (la |el )?(alarma|temporizador)\w*|\bbasta\b",
    ),
    "de": dict(
        time_q=r"\bwie spat ist es\b|\bwie viel uhr\b|\bwieviel uhr\b|\buhrzeit\b|\bwie spat\b|\bwelche zeit\b|\bsag mir die zeit\b|\bwieviel uhr ist es\b",
        date_q=r"\bwelcher tag ist (heute|es)\b|\bwelches datum\b|\bden wievielten\b|\bder wievielte\b|\bwelchen tag haben wir\b|\bwelcher wochentag\b|\bwas ist heute fur ein tag\b|\bdatum heute\b",
        timer=r"\btimer\w*|\bkurzzeitwecker\b|\bcountdown\b",
        alarm=r"\bwecker\w*|\bweck\w* mich\b|\bwecke mich\b",
        set=r"\b(stell|setz|start|mach|erstell|lege|leg|programmier)\w*",
        cancel=r"\b(losch|abbrech|stopp|beend|brich|ausschalt|deaktivier|entfern|schalt\w* aus|aus)\w*",
        pause=r"\b(paus|anhalt)\w*",
        resume=r"\b(weiter|fortsetz|setz\w* fort)\w*",
        left=r"\bwie lange noch\b|\bwie viel zeit\b|\bwieviel zeit\b|\bwie viele minuten\b|\brestzeit\b|\bverbleibend\w*|\bwie lange dauert es noch\b",
        alarm_list=r"\bwelche wecker\b|\bliste (der )?wecker\b|\bzeig\w* (meine )?wecker\b|\bhabe ich einen wecker\b|\bwie viele wecker\b|\bmeine wecker\b|\bwann klingelt mein wecker\b",
        play=r"\b(spiel|abspiel|mach|starte|horen|hor|lass)\w*",
        music=r"\b(musik|lied|lieder|song|songs|titel|album|alben|playlist|radio|jazz|rock|klassik|pop|etwas)\w*",
        radio=r"\bradio\w*|\bsender\b",
        jamendo=r"\bjamendo\b",
        local=r"\bmeine (eigene )?(musik|bibliothek|lieder|playlist|sammlung)\b|\baus meiner (bibliothek|sammlung)\b",
        ctl_pause=r"\b(paus|anhalt)\w*",
        ctl_resume=r"\b(weiter|fortsetz)\w*",
        ctl_stop=r"\bstopp?\b|\bhalt\b|\bbeende (die )?musik\b|\bmusik (aus|stoppen)\b|\bschalt\w* (die )?(musik|radio) aus\b|\bgenug\b|\bruhe\b",
        ctl_next=r"\bnachste\w*|\bubersprin\w*|\bskip\b",
        ctl_prev=r"\bvorherige\w*|\bzuruck\b|\bvorige\w*",
        louder=r"\blauter\b|\bnoch lauter\b",
        quieter=r"\bleiser\b|\bnoch leiser\b",
        vol_set=r"\blautstarke (auf )?",
        unit_hour=r"^stunden?$", unit_minute=r"^minuten?$", unit_second=r"^sekunden?$",
        bare_unit=r"^$",
        halves=[(r"\banderthalb stunden?\b", 5400), (r"\b(eine )?halbe stunde\b", 1800)],
        cue=r"^(um|auf|fur|zu)$", rel=r"\bin\b|\bnach\b", repeat=r"\bjeden\b|\btaglich\b|\bwerktags\b|\bwerktagen\b|\bam wochenende\b|\bjede\w*",
        weekdays=[r"montag\w*", r"dienstag\w*", r"mittwoch\w*", r"donnerstag\w*", r"freitag\w*", r"samstag\w*|sonnabend\w*", r"sonntag\w*"],
        wd_work=r"\bwerktag\w*|\bwerktags\b|\bmontag bis freitag\b", wd_weekend=r"\bwochenende\w*", wd_daily=r"\bjeden tag\b|\btaglich\b",
        tomorrow=r"\bmorgen\b(?! fruh)", after_tomorrow=r"\bubermorgen\b",
        am=r"$^", pm=r"$^", morning=r"\b(morgens|fruh|vormittags|am morgen)\b", afternoon=r"\b(nachmittags|am nachmittag)\b", evening=r"\b(abends|am abend)\b", night=r"\b(nachts|in der nacht)\b",
        all=r"\balle\b", stop_ring=r"\b(schalt|stopp?|mach)\w* (den )?(wecker|timer)\w*|\bstopp?\b",
    ),
    "fr": dict(
        time_q=r"\bquelle heure est il\b|\bil est quelle heure\b|\bquelle heure\b|\bl heure qu il est\b|\bdis moi l heure\b|\bl heure actuelle\b|\bquelle heure on est\b",
        date_q=r"\bquel jour (sommes nous|est on|est ce|on est)\b|\bquelle est la date\b|\bon est le combien\b|\bquelle date\b|\bquel jour de la semaine\b|\bla date d aujourd hui\b",
        timer=r"\bminuteur\w*|\bchrono\w*|\bcompte a rebours\b|\btimer\w*",
        alarm=r"\breveil\w*|\breveille\w* moi\b",
        set=r"\b(mets|met|mettre|lance|lancer|demarre|regle|programme|cree|creer|fixe)\w*",
        cancel=r"\b(annule|supprime|arrete|stoppe|eteins|desactive|efface|retire)\w*",
        pause=r"\bpause\b|\bmets en pause\b|\bsuspend\w*",
        resume=r"\b(reprend|continue|relance)\w*",
        left=r"\bcombien de temps (reste|il reste)\b|\bil reste combien\b|\btemps restant\b|\bcombien il reste\b|\bil reste combien de temps\b",
        alarm_list=r"\bquels reveils\b|\bliste (des )?reveils\b|\bmontre\w* (mes )?reveils\b|\bai je un reveil\b|\bcombien de reveils\b|\bmes reveils\b|\bquand sonne mon reveil\b",
        play=r"\b(joue|jouer|mets|met|lance|lancer|ecouter|passe|diffuse|mettre)\w*|\bje veux (ecouter|entendre)\b",
        music=r"\b(musique|chanson|morceau|titre|album|playlist|radio|jazz|rock|classique|pop|quelque chose)\w*",
        radio=r"\bradio\w*|\bstation\b",
        jamendo=r"\bjamendo\b",
        local=r"\bma (propre )?(musique|bibliotheque|playlist|collection)\b|\bmes (chansons|morceaux)\b|\bde ma (bibliotheque|collection)\b",
        ctl_pause=r"\bpause\b|\bsuspend\w*",
        ctl_resume=r"\b(reprend|continue|relance)\w*",
        ctl_stop=r"\bstop\b|\barrete\w*|\bstoppe\w*|\beteins (la )?(musique|radio)\b|\bca suffit\b|\bsilence\b",
        ctl_next=r"\bsuivant\w*|\bsaute\w*|\bpasse\b|\bprochain\w*",
        ctl_prev=r"\bprecedent\w*|\bretour\b|\breviens\b",
        louder=r"\bplus fort\b|\bmonte\w* (le )?(son|volume)\b|\baugmente\w* (le )?(son|volume)\b",
        quieter=r"\bmoins fort\b|\bbaisse\w* (le )?(son|volume)\b|\bdiminue\w* (le )?(son|volume)\b",
        vol_set=r"\bvolume (a |sur |au )?",
        unit_hour=r"^heures?$", unit_minute=r"^minutes?$", unit_second=r"^secondes?$",
        bare_unit=r"^$",
        halves=[(r"\bheure et demie\b", 5400), (r"\b(une )?demi heure\b", 1800)],
        cue=r"^(a|pour|sur)$", rel=r"\bdans\b|\bapres\b", repeat=r"\bchaque\b|\btous les\b|\btoutes les\b|\ben semaine\b|\ble week end\b|\bles (lundis|mardis|mercredis|jeudis|vendredis|samedis|dimanches)\b",
        weekdays=[r"lundis?", r"mardis?", r"mercredis?", r"jeudis?", r"vendredis?", r"samedis?", r"dimanches?"],
        wd_work=r"\ben semaine\b|\bjours ouvrables\b|\bdu lundi au vendredi\b", wd_weekend=r"\bweek end\b|\bweekend\b", wd_daily=r"\btous les jours\b|\bchaque jour\b|\bquotidien\w*",
        tomorrow=r"\bdemain\b", after_tomorrow=r"\bapres demain\b",
        am=r"$^", pm=r"$^", morning=r"\b(du matin|le matin)\b", afternoon=r"\b(de l apres midi|l apres midi)\b", evening=r"\b(du soir|le soir)\b", night=r"\b(de la nuit|cette nuit)\b",
        all=r"\btous\b|\btoutes\b", stop_ring=r"\b(arrete|eteins|coupe)\w* (le |la |l )?(reveil|minuteur)\w*|\bstop\b",
    ),
}
_C = {lang: {k: (re.compile(v) if isinstance(v, str) else v) for k, v in d.items()} for lang, d in LANG.items()}
for _lang, _d in _C.items():
    _d["halves"] = [(re.compile(p), s) for p, s in LANG[_lang]["halves"]]
    _d["weekdays"] = [re.compile(r"\b(?:%s)\b" % p) for p in LANG[_lang]["weekdays"]]
    _d["bare_unit"] = re.compile(LANG[_lang]["bare_unit"]) if LANG[_lang]["bare_unit"] != r"^$" else None


# ------------------------------------------------------------------ вспомогательный разбор
def _unit_seconds(token: str, L: dict) -> int | None:
    if L["unit_hour"].match(token):
        return 3600
    if L["unit_minute"].match(token):
        return 60
    if L["unit_second"].match(token):
        return 1
    return None


def parse_duration(t: str, lang: str) -> int:
    """Суммарная длительность в секундах из фразы («1 час 30 минут», «полчаса», «five minutes»); 0 — не найдено."""
    L = _C[lang]
    total = 0
    for rx, secs in L["halves"]:
        if rx.search(t):
            total += secs
            t = rx.sub(" ", t)
    tokens = t.split()
    covered: set[int] = set()
    for s, e, val in find_numbers(tokens, lang):
        covered.update(range(s, e))
        if e < len(tokens) and (mult := _unit_seconds(tokens[e], L)):
            total += val * mult
            covered.add(e)
            if mult == 3600:                    # «час тридцать», «une heure trente» — число без единицы после часа = минуты
                nxt = next(((s2, e2, v2) for s2, e2, v2 in find_numbers(tokens, lang) if s2 == e + 1), None)
                if nxt and nxt[2] < 60 and (nxt[1] >= len(tokens) or _unit_seconds(tokens[nxt[1]], L) is None):
                    total += nxt[2] * 60
                    covered.update(range(nxt[0], nxt[1]))
    if L["bare_unit"]:                          # «на минуту», «на час» — без числа
        for i, tok in enumerate(tokens):
            if i not in covered and L["bare_unit"].match(tok):
                total += _unit_seconds(tok, L)
    return total


def _hour_with_part(h: int, L: dict, t: str) -> tuple[int, bool]:
    """(час 0..23, известна ли половина суток)."""
    if h > 12:
        return h % 24, True
    if L["morning"].search(t) or L["am"].search(t):
        return (0 if h == 12 else h), True
    if L["afternoon"].search(t):
        return (12 if h == 12 else h + 12), True
    if L["evening"].search(t) or L["pm"].search(t):
        return (12 if h == 12 else h + 12), True
    if L["night"].search(t):
        return (0 if h == 12 else (h if h < 6 else h + 12)), True
    return h % 24, h == 0


def parse_clock(t: str, lang: str) -> dict | None:
    """Время суток из фразы: {"hour", "minute", "known": bool}; known=False — час 1..12 без утра/вечера."""
    L = _C[lang]
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
    if m and int(m.group(1)) <= 24 and int(m.group(2)) < 60:
        h, known = _hour_with_part(int(m.group(1)), L, t)
        return {"hour": h, "minute": int(m.group(2)), "known": known or int(m.group(1)) > 12 or int(m.group(1)) == 0}
    tokens = t.split()
    nums = find_numbers(tokens, lang)
    # «half past seven», «halb acht», «siete y media», «sept heures et demie» ...
    special = _special_clock(t, tokens, nums, lang)
    if special:
        h, minute = special
        h2, known = _hour_with_part(h, L, t)
        return {"hour": h2, "minute": minute, "known": known or h > 12}
    for idx, (s, e, val) in enumerate(nums):
        if val > 24 or (e < len(tokens) and _unit_seconds(tokens[e], L) not in (None, 3600)):
            continue                                         # «5 минут» — длительность, а не час
        if s > 0 and L["rel"].search(tokens[s - 1]):
            continue
        if tokens[s] in ALIAS_WORDS[lang] and not (
                (e < len(tokens) and _unit_seconds(tokens[e], L) == 3600) or (lang == "es" and s > 0 and tokens[s - 1] == "la")):
            continue                                         # «una alarma», «a timer» — не час
        minute, j = 0, e
        if j < len(tokens) and _unit_seconds(tokens[j], L) == 3600 or (j < len(tokens) and tokens[j] in ("uhr", "h", "heures", "heure")):
            j += 1
        nxt = next(((s2, e2, v2) for s2, e2, v2 in nums if s2 == j), None)
        if nxt and nxt[2] < 60 and (nxt[1] >= len(tokens) or _unit_seconds(tokens[nxt[1]], L) in (None, 60)):
            minute = nxt[2]
        elif nxt is None and j < len(tokens) and tokens[j] in ("ровно", "o", "clock"):
            pass
        h, known = _hour_with_part(val, L, t)
        return {"hour": h, "minute": minute, "known": known or val > 12 or val == 0}
    return None


def _special_clock(t: str, tokens: list[str], nums: list, lang: str) -> tuple[int, int] | None:
    def hour_after(word_rx: str):
        m = re.search(word_rx, t)
        if not m:
            return None
        pos = len(t[:m.end()].split())
        for s, e, v in nums:
            if s >= pos and v <= 12:
                return v
        return None

    if lang == "en":
        if (h := hour_after(r"\bhalf past\b")) is not None:
            return h, 30
        if (h := hour_after(r"\bquarter past\b")) is not None:
            return h, 15
        if (h := hour_after(r"\bquarter to\b")) is not None:
            return (h - 1) % 24 or 12, 45
    if lang == "de":
        if (h := hour_after(r"\bhalb\b")) is not None:
            return (h - 1) or 12, 30
        if (h := hour_after(r"\bviertel nach\b")) is not None:
            return h, 15
        if (h := hour_after(r"\bviertel vor\b")) is not None:
            return (h - 1) or 12, 45
    if lang == "es":
        for s, e, v in nums:
            tail = " ".join(tokens[e:e + 3])
            if v <= 12 and tail.startswith("y media"):
                return v, 30
            if v <= 12 and tail.startswith("y cuarto"):
                return v, 15
            if v <= 12 and tail.startswith("menos cuarto"):
                return (v - 1) or 12, 45
    if lang == "fr":
        for s, e, v in nums:
            tail = " ".join(tokens[e:e + 4])
            if v <= 24 and re.match(r"(heures? )?et demie?\b", tail):
                return v, 30
            if v <= 24 and re.match(r"(heures? )?et quart\b", tail):
                return v, 15
            if v <= 24 and re.match(r"(heures? )?moins le quart\b", tail):
                return (v - 1) % 24, 45
    return None


def parse_weekdays(t: str, lang: str) -> tuple[list[int], bool]:
    """(дни недели 0..6, повторять ли)."""
    L = _C[lang]
    days: list[int] = []
    if L["wd_daily"].search(t):
        return list(range(7)), True
    if L["wd_work"].search(t):
        return [0, 1, 2, 3, 4], True
    if L["wd_weekend"].search(t):
        return [5, 6], True
    for i, rx in enumerate(L["weekdays"]):
        if rx.search(t):
            days.append(i)
    return days, bool(days) and bool(L["repeat"].search(t))


def _words_to_query(t: str, L: dict, drop: list[str]) -> str:
    """Остаток фразы после удаления глаголов, служебных слов и источников — поисковый запрос для музыки."""
    for key in drop:
        t = L[key].sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


FILLERS = {
    "ru": r"\b(пожалуйста|мне|для меня|сейчас|что нибудь|какую нибудь|какой нибудь|песню|песни|песен|трек|композицию|музыку|музыка|группы|группу|исполнителя|артиста|альбом|от|из|на|в|по|мою|мои|свою|нильс|нильса)\b",
    "en": r"\b(please|some|any|a|an|the|me|for me|by|from|of|something|music|song|songs|track|tracks|album|playlist|radio|station|on|my|can you|could you|nils)\b",
    "es": r"\b(por favor|algo de|algo|un|una|el|la|los|las|de|del|mi|mis|musica|cancion|canciones|tema|disco|album|playlist|radio|emisora|en|para mi|nils)\b",
    "de": r"\b(bitte|etwas|ein|eine|einen|von|vom|der|die|das|mir|musik|lied|lieder|song|songs|titel|album|playlist|radio|sender|auf|meine|meiner|aus|nils|mal)\b",
    "fr": r"\b(s il te plait|s il vous plait|quelque chose de|quelque chose|un|une|le|la|les|de|du|des|d|ma|mes|musique|chanson|morceau|titre|album|playlist|radio|station|sur|pour moi|nils)\b",
}


def _music_intent(t: str, lang: str) -> Intent | None:
    L = _C[lang]
    if not (L["play"].search(t) and (L["music"].search(t) or L["jamendo"].search(t))):
        return None
    source = "radio" if L["radio"].search(t) else "jamendo" if L["jamendo"].search(t) else "local" if L["local"].search(t) else None
    q = _words_to_query(t, L, ["play", "radio", "jamendo", "local"])
    q = re.sub(FILLERS[lang], " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return Intent("music_play", {"query": q, "source": source})


def _volume_intent(t: str, lang: str) -> Intent | None:
    L = _C[lang]
    m = L["vol_set"].search(t)
    if m:
        tokens = t[m.end():].split()
        for s, e, v in find_numbers(tokens, lang):
            if s == 0 and 0 <= v <= 100:
                return Intent("volume_set", {"value": v})
        digits = re.search(r"\b(\d{1,3})\b", t)
        if digits and int(digits.group(1)) <= 100:
            return Intent("volume_set", {"value": int(digits.group(1))})
    if L["louder"].search(t):
        return Intent("volume_up")
    if L["quieter"].search(t):
        return Intent("volume_down")
    return None


PLACE_WORDS = {"ru": r"\b(в|во|для)\s+\w+", "en": r"\b(in|at|for)\s+\w+", "es": r"\b(en|de)\s+\w+",
               "de": r"\b(in|bei|auf)\s+\w+", "fr": r"\b(a|au|en|dans|pour)\s+\w+"}


def _other_place(rest: str, lang: str) -> bool:
    """«который час в Токио» — вопрос не про местное время устройства, оставляем LLM."""
    return bool(re.search(PLACE_WORDS[lang], rest))



# ------------------------------------------------------------------ погода
# word — вопрос о погоде; place — предлог перед названием места (остаток фразы); stop — слова, которыми название заканчивается.
# Шаблоны для нормализованного текста (нижний регистр, без диакритики у es/de/fr, дефисы → пробелы).
WEATHER = {
    "ru": dict(
        word=r"\bпогод\w*|\bпрогноз\w* (погод\w*|на сегодня|на день)|\bсколько (сейчас )?градус\w*|\bтемператур\w* (на улице|за окном|сегодня|воздуха)\b"
             r"|\bбудет ли (дождь|снег|гроза)\b|\bидет ли (дождь|снег)\b|\bнужен ли (мне )?зонт\w*|\bхолодно ли\b|\bтепло ли\b",
        place=r"\b(?:в|во|для)\s+(.+)$",
        stop={"сегодня", "сейчас", "завтра", "послезавтра", "пожалуйста", "улице", "там", "какая", "будет", "днем", "вечером", "утром",
              "ночью", "скажи", "мне", "день", "ли", "и", "что", "как", "погода", "погоду", "погоды", "сегодняшний", "сегодняшняя", "ближайшие"}),
    "en": dict(
        word=r"\bweather\b|\bforecast\b|\bhow (hot|cold|warm) is it\b|\bis it (going to )?(rain|snow)\w*|\bwill it (rain|snow)\b"
             r"|\btemperature (outside|today|now)\b|\bdo i need an umbrella\b",
        place=r"\b(?:in|for|at)\s+(.+)$",
        stop={"today", "now", "right", "tomorrow", "please", "outside", "like", "going", "be", "tonight", "this", "morning", "afternoon",
              "evening", "and", "there", "is", "the", "weather", "forecast", "tell", "me", "currently"}),
    "es": dict(
        word=r"\bque tiempo (hace|hara|va a hacer)\b|\bel tiempo (en|para|hoy|ahora|que)\b|\bclima\b|\bpronostico\b|\bva a llover\b|\bllovera\b"
             r"|\bnecesito (un )?paraguas\b",
        place=r"\b(?:en|para)\s+(.+)$",
        stop={"hoy", "ahora", "manana", "por", "favor", "esta", "tarde", "noche", "fuera", "afuera", "que", "tiempo", "hace", "hara", "va",
              "a", "hacer", "el", "clima", "dime", "me", "actualmente"}),
    "de": dict(
        word=r"\bwetter\w*|\bwie (warm|kalt|heiss) ist es\b|\bwird es (regnen|schneien)\b|\bbrauche ich einen regenschirm\b",
        place=r"\b(?:in|fur)\s+(.+)$",
        stop={"heute", "jetzt", "morgen", "bitte", "draussen", "aussen", "wie", "ist", "das", "wird", "denn", "eigentlich", "gerade",
              "aktuell", "wetter", "sag", "mir", "es"}),
    "fr": dict(
        word=r"\bmeteo\b|\bquel temps\b|\bil (va )?(pleuvoir|neiger)\b|\bfait il (chaud|froid)\b|\bai je besoin d un parapluie\b",
        place=r"\b(?:a|au|aux|en|pour)\s+(.+)$",
        stop={"aujourd'hui", "maintenant", "demain", "svp", "s'il", "te", "plait", "vous", "ce", "matin", "soir", "dehors", "dis", "moi",
              "quel", "temps", "fait", "il", "et", "la", "meteo", "actuellement"}),
}
_W = {lang: {"word": re.compile(d["word"]), "place": re.compile(d["place"]), "stop": d["stop"]} for lang, d in WEATHER.items()}
MAX_PLACE_TOKENS = 5
TRAILING_WORDS = {"на", "в", "во", "и", "in", "on", "at", "and", "a", "au", "aux", "de", "en", "pour", "para", "fur", "for", "the", "la", "le", "el"}


def _weather_intent(t: str, tokens: list[str], lang: str) -> Intent | None:
    W, L = _W[lang], _C[lang]
    if len(tokens) > 14 or not W["word"].search(t):
        return None
    place = ""
    m = W["place"].search(t)
    if m:
        words = []
        for tok in m.group(1).split():
            if tok in W["stop"]:
                break
            words.append(tok)
        while words and words[-1] in TRAILING_WORDS:         # «в казани на сегодня» → «казани»; «ростове на дону» остаётся целиком
            words.pop()
        if len(words) > MAX_PLACE_TOKENS:
            return None                                   # длинный оборот — не название места: пусть отвечает LLM
        place = " ".join(words)
    day = 2 if L["after_tomorrow"].search(t) else 1 if L["tomorrow"].search(t) else 0
    return Intent("weather", {"place": place, "day": day})


# ------------------------------------------------------------------ главная функция
def parse(text: str, lang: str) -> Intent | None:
    if lang not in _C or not text:
        return None
    L = _C[lang]
    t = normalize(text, lang)
    if not t:
        return None
    tokens = t.split()
    short = len(tokens) <= SHORT_COMMAND_TOKENS
    has_timer, has_alarm = bool(L["timer"].search(t)), bool(L["alarm"].search(t))

    # ---- будильники
    if has_alarm and not has_timer:
        if L["alarm_list"].search(t):
            return Intent("alarm_list")
        clock0 = parse_clock(t, lang)
        if L["cancel"].search(t) and not (L["set"].search(t) and clock0):
            return Intent("alarm_cancel", {"all": bool(L["all"].search(t)), "clock": clock0})
        rel_secs = 0
        if L["rel"].search(t):
            rel_secs = parse_duration(t, lang)
        clock = parse_clock(t, lang) if not rel_secs else None
        days, repeat = parse_weekdays(t, lang)
        offset = 2 if L["after_tomorrow"].search(t) else 1 if L["tomorrow"].search(t) else 0
        if clock or rel_secs or L["set"].search(t) or re.search(r"\bразбуди|\bwake me|\bdespiertame|\bweck|\breveille", t):
            return Intent("alarm_set", {"clock": clock, "relative_seconds": rel_secs, "weekdays": days,
                                        "repeat": repeat, "day_offset": offset})
        return None

    # ---- таймеры
    if has_timer:
        if L["left"].search(t):
            return Intent("timer_query")
        if L["pause"].search(t):
            return Intent("timer_pause")
        if L["resume"].search(t):
            return Intent("timer_resume")
        secs = parse_duration(t, lang)
        if L["cancel"].search(t) and not (L["set"].search(t) and secs):
            return Intent("timer_cancel", {"all": bool(L["all"].search(t)), "seconds": secs})
        if secs or L["set"].search(t):
            return Intent("timer_set", {"seconds": secs})
        return None
    if L["left"].search(t) and (short or len(tokens) <= 8):
        return Intent("timer_query")

    # ---- время и дата
    if (m := L["time_q"].search(t)) and not _other_place(t[m.end():], lang):
        return Intent("time")
    if L["date_q"].search(t) and not re.search(r"\d", t):
        return Intent("date")

    # ---- музыка и громкость
    if (it := _music_intent(t, lang)):
        return it
    if (it := _volume_intent(t, lang)) and len(tokens) <= 8:
        return it
    # ---- погода
    if (it := _weather_intent(t, tokens, lang)):
        return it
    if short:   # короткие общие команды
        if L["ctl_resume"].search(t):
            return Intent("music_resume")
        if L["ctl_pause"].search(t):
            return Intent("music_pause")
        if L["ctl_next"].search(t):
            return Intent("music_next")
        if L["ctl_prev"].search(t):
            return Intent("music_prev")
        if L["ctl_stop"].search(t):
            return Intent("stop")
    return None
