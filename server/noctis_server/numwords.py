"""Числа словами и обратно для 5 языков (ru en es de fr), 0..999.

Единственный источник правды — `to_words`; для разбора речи строится обратный словарь «слова → число»
(`find_numbers`), поэтому произносимые и распознаваемые формы всегда согласованы. Нужны, потому что TTS Silero
плохо читает цифры, а Whisper может вернуть и цифры, и слова.
"""
import re
import unicodedata

LANGS = ("ru", "en", "es", "de", "fr")


# ------------------------------------------------------------------ нормализация текста
def normalize(text: str, lang: str) -> str:
    """Нижний регистр, без знаков препинания (кроме ':' между цифрами), дефисы → пробелы; для не-русских — без диакритики."""
    t = text.lower().replace("ё", "е")
    if lang != "ru":
        t = t.replace("ß", "ss")
        t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    t = t.replace("-", " ").replace("’", "'")
    t = re.sub(r"(?<=\d)[.,](?=\d{2}\b)", ":", t)            # 7.30 → 7:30
    t = re.sub(r"[^\w:' ]", " ", t, flags=re.UNICODE)
    t = re.sub(r"(?<!\d):|:(?!\d)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ------------------------------------------------------------------ числа словами
_RU_U = "ноль один два три четыре пять шесть семь восемь девять".split()
_RU_T = "десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать".split()
_RU_D = "_ _ двадцать тридцать сорок пятьдесят шестьдесят семьдесят восемьдесят девяносто".split()
_RU_H = "_ сто двести триста четыреста пятьсот шестьсот семьсот восемьсот девятьсот".split()

_EN_U = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_EN_D = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()

_ES_U = ("cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis diecisiete "
         "dieciocho diecinueve veinte veintiuno veintidós veintitrés veinticuatro veinticinco veintiséis veintisiete "
         "veintiocho veintinueve").split()
_ES_D = "_ _ _ treinta cuarenta cincuenta sesenta setenta ochenta noventa".split()
_ES_H = "_ ciento doscientos trescientos cuatrocientos quinientos seiscientos setecientos ochocientos novecientos".split()

_DE_U = ("null eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf dreizehn vierzehn fünfzehn sechzehn "
         "siebzehn achtzehn neunzehn").split()
_DE_D = "_ _ zwanzig dreißig vierzig fünfzig sechzig siebzig achtzig neunzig".split()

_FR_U = ("zéro un deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze quinze seize "
         "dix-sept dix-huit dix-neuf").split()
_FR_D = "_ _ vingt trente quarante cinquante soixante".split()


def _ru(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(_RU_H[n // 100])
        n %= 100
    if n >= 20:
        parts.append(_RU_D[n // 10])
        n %= 10
        if n:
            parts.append(_RU_U[n])
    elif n >= 10:
        parts.append(_RU_T[n - 10])
    elif n or not parts:
        parts.append(_RU_U[n])
    return " ".join(parts)


def _en(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(_EN_U[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        parts.append(_EN_D[n // 10] + (" " + _EN_U[n % 10] if n % 10 else ""))
    elif n or not parts:
        parts.append(_EN_U[n])
    return " ".join(parts)


def _es(n: int) -> str:
    if n == 100:
        return "cien"
    parts = []
    if n >= 100:
        parts.append(_ES_H[n // 100])
        n %= 100
    if n >= 30:
        parts.append(_ES_D[n // 10] + (" y " + _ES_U[n % 10] if n % 10 else ""))
    elif n or not parts:
        parts.append(_ES_U[n])
    return " ".join(parts)


def _de(n: int) -> str:
    if n == 0:
        return "null"
    s = ""
    if n >= 100:
        s += ("ein" if n // 100 == 1 else _DE_U[n // 100]) + "hundert"
        n %= 100
    if n >= 20:
        unit = n % 10
        s += (("ein" if unit == 1 else _DE_U[unit]) + "und" if unit else "") + _DE_D[n // 10]
    elif n:
        s += _DE_U[n]
    return s


def _fr(n: int) -> str:
    parts = []
    if n >= 100:
        h = n // 100
        parts.append("cent" if h == 1 else _FR_U[h] + " cent")
        n %= 100
        if n == 0:
            return parts[0] + ("s" if h > 1 else "")
    if n == 0:
        parts.append(_FR_U[0])
    elif n < 20:
        parts.append(_FR_U[n])
    elif n < 70:
        t, u = divmod(n, 10)
        parts.append(_FR_D[t] + (" et un" if u == 1 else ("-" + _FR_U[u] if u else "")))
    elif n < 80:   # 70..79 = soixante + 10..19
        parts.append("soixante" + (" et onze" if n == 71 else "-" + _FR_U[n - 60]))
    else:          # 80..99 = quatre-vingt(s) + 0..19
        parts.append("quatre-vingts" if n == 80 else "quatre-vingt-" + _FR_U[n - 80])
    return " ".join(parts)


_TO = {"ru": _ru, "en": _en, "es": _es, "de": _de, "fr": _fr}


def to_words(n: int, lang: str) -> str:
    """Число 0..999 словами в нейтральной форме (ru: муж. р.). Вне диапазона — цифрами."""
    if not 0 <= n <= 999:
        return str(n)
    return _TO[lang](n)


def agree(words: str, lang: str, feminine: bool) -> str:
    """Согласование «один/два» с существительным (последнее слово числительного)."""
    head, _, last = words.rpartition(" ")
    sep = " " if head else ""
    if lang == "ru":
        if feminine:
            last = {"один": "одна", "два": "две"}.get(last, last)
    elif lang == "es":
        if last.endswith("uno"):
            last = last[:-3] + ("una" if feminine else "un")
    elif lang == "de":
        if last == "eins":
            last = "eine" if feminine else "ein"
    elif lang == "fr":
        if feminine and last == "un":
            last = "une"
    return head + sep + last if head else last


# ------------------------------------------------------------------ существительные: час / минута / секунда
UNITS = {
    "ru": {"hour": ("час", "часа", "часов"), "minute": ("минута", "минуты", "минут"), "second": ("секунда", "секунды", "секунд")},
    "en": {"hour": ("hour", "hours"), "minute": ("minute", "minutes"), "second": ("second", "seconds")},
    "es": {"hour": ("hora", "horas"), "minute": ("minuto", "minutos"), "second": ("segundo", "segundos")},
    "de": {"hour": ("Stunde", "Stunden"), "minute": ("Minute", "Minuten"), "second": ("Sekunde", "Sekunden")},
    "fr": {"hour": ("heure", "heures"), "minute": ("minute", "minutes"), "second": ("seconde", "secondes")},
}
FEMININE = {
    "ru": {"hour": False, "minute": True, "second": True},
    "en": {"hour": False, "minute": False, "second": False},
    "es": {"hour": True, "minute": False, "second": False},
    "de": {"hour": True, "minute": True, "second": True},
    "fr": {"hour": True, "minute": True, "second": True},
}


def noun(n: int, unit: str, lang: str) -> str:
    forms = UNITS[lang][unit]
    if lang == "ru":
        if n % 10 == 1 and n % 100 != 11:
            return forms[0]
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return forms[1]
        return forms[2]
    return forms[0] if n == 1 else forms[1]


def count_phrase(n: int, unit: str, lang: str) -> str:
    """«пять минут», «одна минута», «two hours», «una hora»..."""
    return f"{agree(to_words(n, lang), lang, FEMININE[lang][unit])} {noun(n, unit, lang)}"


def duration_words(seconds: int, lang: str) -> str:
    """Длительность словами: «1 час 5 минут» → «один час пять минут»."""
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    parts = [count_phrase(v, u, lang) for v, u in ((h, "hour"), (m, "minute"), (s, "second")) if v]
    if not parts:
        parts = [count_phrase(0, "second", lang)]
    return " ".join(parts)


# ------------------------------------------------------------------ разбор: слова → число
_REVERSE: dict[str, dict[str, int]] = {}
_ALIASES = {
    "ru": {"одна": 1, "одну": 1, "одно": 1, "две": 2},
    "en": {"a": 1, "an": 1},
    "es": {"un": 1, "una": 1, "veintiun": 21, "veintiuna": 21},
    "de": {"ein": 1, "eine": 1, "einen": 1},
    "fr": {"une": 1},
}


# слова, которые читаются и как «1», и как артикль («una alarma», «un minuteur») — в разборе времени суток им не верим
ALIAS_WORDS = {"ru": set(), "en": {"a", "an"}, "es": {"un", "una"}, "de": {"ein", "eine", "einen"}, "fr": {"un", "une"}}


def _reverse(lang: str) -> dict[str, int]:
    if lang not in _REVERSE:
        table: dict[str, int] = {}
        for n in range(1000):
            w = normalize(to_words(n, lang), lang)
            table.setdefault(w, n)
            if lang == "de":   # «zweihundert fünf» тоже понимаем
                for h in ("hundert",):
                    if h in w and not w.endswith(h):
                        head, _, tail = w.partition(h)
                        table.setdefault(f"{head}{h} {tail}", n)
        table.update(_ALIASES[lang])
        _REVERSE[lang] = table
    return _REVERSE[lang]


MAX_SPAN = 6


def find_numbers(tokens: list[str], lang: str) -> list[tuple[int, int, int]]:
    """Числа в списке токенов (уже нормализованных): [(начало, конец, значение)]. Цифры и слова; самые длинные совпадения."""
    table = _reverse(lang)
    out, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.isdigit():
            out.append((i, i + 1, int(tok)))
            i += 1
            continue
        for span in range(min(MAX_SPAN, len(tokens) - i), 0, -1):
            value = table.get(" ".join(tokens[i:i + span]))
            if value is not None:
                out.append((i, i + span, value))
                i += span
                break
        else:
            i += 1
    return out
