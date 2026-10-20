"""Числа словами и обратно: полный круг 0..999 для 5 языков, согласование по роду, разбор из речи."""
import pytest

from noctis_server import numwords as nw

EXPECTED = {
    "ru": {0: "ноль", 11: "одиннадцать", 21: "двадцать один", 100: "сто", 215: "двести пятнадцать"},
    "en": {0: "zero", 13: "thirteen", 21: "twenty one", 100: "one hundred", 215: "two hundred fifteen"},
    "es": {0: "cero", 16: "dieciséis", 21: "veintiuno", 35: "treinta y cinco", 100: "cien", 101: "ciento uno"},
    "de": {0: "null", 21: "einundzwanzig", 35: "fünfunddreißig", 100: "einhundert", 215: "zweihundertfünfzehn"},
    "fr": {0: "zéro", 21: "vingt et un", 71: "soixante et onze", 80: "quatre-vingts", 91: "quatre-vingt-onze", 35: "trente-cinq", 72: "soixante-douze",
           200: "deux cents"},
}


@pytest.mark.parametrize("lang", nw.LANGS)
def test_known_forms(lang):
    for n, words in EXPECTED[lang].items():
        assert nw.to_words(n, lang) == words


@pytest.mark.parametrize("lang", nw.LANGS)
def test_roundtrip_0_999(lang):
    for n in range(1000):
        tokens = nw.normalize(nw.to_words(n, lang), lang).split()
        assert nw.find_numbers(tokens, lang) == [(0, len(tokens), n)], (lang, n, tokens)


def test_out_of_range_falls_back_to_digits():
    assert nw.to_words(1000, "ru") == "1000" and nw.to_words(-1, "en") == "-1"


def test_russian_plurals_and_gender():
    assert nw.count_phrase(1, "minute", "ru") == "одна минута"
    assert nw.count_phrase(2, "minute", "ru") == "две минуты"
    assert nw.count_phrase(5, "minute", "ru") == "пять минут"
    assert nw.count_phrase(21, "minute", "ru") == "двадцать одна минута"
    assert nw.count_phrase(11, "minute", "ru") == "одиннадцать минут"
    assert nw.count_phrase(2, "hour", "ru") == "два часа" and nw.count_phrase(1, "hour", "ru") == "один час"
    assert nw.count_phrase(22, "second", "ru") == "двадцать две секунды"


def test_other_languages_agreement():
    assert nw.count_phrase(1, "minute", "es") == "un minuto" and nw.count_phrase(1, "hour", "es") == "una hora"
    assert nw.count_phrase(21, "minute", "es") == "veintiun minutos"
    assert nw.count_phrase(1, "minute", "de") == "eine Minute" and nw.count_phrase(1, "minute", "fr") == "une minute"
    assert nw.count_phrase(21, "minute", "fr") == "vingt et une minutes"
    assert nw.count_phrase(1, "hour", "en") == "one hour" and nw.count_phrase(2, "hour", "en") == "two hours"


def test_duration_words():
    assert nw.duration_words(3600 + 5 * 60 + 1, "ru") == "один час пять минут одна секунда"
    assert nw.duration_words(90, "en") == "one minute thirty seconds"
    assert nw.duration_words(0, "de") == "null Sekunden"


def test_normalize():
    assert nw.normalize("Поставь таймер, на 5 минут!", "ru") == "поставь таймер на 5 минут"
    assert nw.normalize("Réveille-moi à 7.30", "fr") == "reveille moi a 7:30"
    assert nw.normalize("Straße für Müller", "de") == "strasse fur muller"
    assert nw.normalize("Ёлка", "ru") == "елка"


def test_find_numbers_digits_and_words_mixed():
    tokens = "через 2 часа тридцать пять минут".split()
    assert nw.find_numbers(tokens, "ru") == [(1, 2, 2), (3, 5, 35)]
