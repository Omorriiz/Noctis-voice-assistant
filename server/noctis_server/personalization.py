"""Применение персонализации к голосовому конвейеру. Чистая логика: без GPU и тяжёлых библиотек,
поэтому тестируется напрямую. Входные «настройки устройства» — dict из DeviceSettingsOut."""
import re
from .catalog import (CHARACTERS, DEFAULT_CHARACTER, DEFAULT_VOICE_GENDER, LANGUAGE_ENGLISH, LANGUAGES,
                      VOICES)

DEFAULT_LANGUAGE = "ru"


def normalize(device_settings: dict | None) -> dict:
    """Язык, пол голоса и характер, приведённые к допустимым значениям (иначе — умолчания)."""
    s = device_settings or {}
    language = s.get("language") if s.get("language") in LANGUAGES else DEFAULT_LANGUAGE
    gender = s.get("voice_gender") if s.get("voice_gender") in ("male", "female") else DEFAULT_VOICE_GENDER
    character = s.get("character_id") if s.get("character_id") in CHARACTERS else DEFAULT_CHARACTER
    return {"language": language, "voice_gender": gender, "character_id": character}


def voice_for(device_settings: dict | None) -> tuple[str, str]:
    """(язык, имя голоса Silero) по языку и полу устройства."""
    n = normalize(device_settings)
    return n["language"], VOICES[n["language"]][n["voice_gender"]]


# ---------------------------------------------------------------- режимы длины ответа
# (максимум слов, num_predict)
MODES = {"short": (15, 80), "medium": (50, 250), "long": (150, 600)}

LONG_TRIGGERS = {
    "ru": ["подробно", "подробнее", "поподробнее", "развёрнуто", "развернуто", "расскажи", "объясни",
           "в деталях", "детально", "почему", "как работает", "в чём разница", "в чем разница", "сравни",
           "лекция", "история", "теория"],
    "en": ["in detail", "explain", "tell me about", "why ", "how does", "how do", "difference between",
           "compare", "history of", "story"],
    "es": ["en detalle", "explica", "cuéntame", "por qué", "cómo funciona", "diferencia entre", "compara",
           "historia"],
    "de": ["ausführlich", "erkläre", "erzähl", "warum", "wie funktioniert", "unterschied zwischen",
           "vergleiche", "geschichte"],
    "fr": ["en détail", "explique", "raconte", "pourquoi", "comment fonctionne", "différence entre",
           "compare", "histoire"],
}
MEDIUM_TRIGGERS = {
    "ru": ["что такое", "кто такой", "кто такая", "опиши", "назови", "перечисли", "какие", "чем отличается"],
    "en": ["what is", "what are", "who is", "describe", "name ", "list "],
    "es": ["qué es", "quién es", "describe", "nombra", "enumera"],
    "de": ["was ist", "wer ist", "beschreibe", "nenne", "zähle auf"],
    "fr": ["qu'est-ce que", "qui est", "décris", "nomme", "énumère"],
}


def choose_mode(user_text: str, language: str) -> str:
    text = user_text.lower()
    if any(t in text for t in LONG_TRIGGERS.get(language, [])):
        return "long"
    if any(t in text for t in MEDIUM_TRIGGERS.get(language, [])):
        return "medium"
    return "short"


# ---------------------------------------------------------------- системный промпт
BASE_PROMPT = {
    "ru": "Ты голосовой ассистент по имени Нильс. {length} Без markdown, без эмодзи, без списков. "
          "Пиши только русскими буквами, без цифр и латиницы. Все числа, даты, время, проценты, денежные суммы "
          "и единицы измерения пиши словами в правильном падеже (например: «двадцать пять процентов», а не «25%»). "
          "Иностранные слова, названия и бренды пиши русской транслитерацией, как они произносятся по-русски "
          "(Coca-Cola — Кока-Кола, Netflix — Нетфликс). "
          "Шутки, анекдоты, сказки и истории придумывай на самые разные бытовые темы: животные, еда, семья, дорога, погода, "
          "школа, природа, сказочные герои. Не делай их про программирование, компьютеры и технологии, "
          "если пользователь сам об этом не просил.",
    "en": "You are Nils, a voice assistant. {length} No markdown, no emoji, no lists. "
          "Your reply is spoken aloud: write numbers, dates, times and units as words, avoid symbols and URLs. "
          "Jokes, riddles and stories should be about everyday topics (animals, food, family, travel, weather, school, nature, fairy-tale characters), not about programming, computers or technology unless the user asks. "
          "Always answer in English.",
    "es": "You are Nils, a voice assistant. {length} No markdown, no emoji, no lists. "
          "Your reply is spoken aloud: write numbers, dates, times and units as words, avoid symbols and URLs. "
          "Jokes, riddles and stories should be about everyday topics (animals, food, family, travel, weather, school, nature, fairy-tale characters), not about programming, computers or technology unless the user asks. "
          "Always answer in Spanish (español), whatever language the user's text looks like.",
    "de": "You are Nils, a voice assistant. {length} No markdown, no emoji, no lists. "
          "Your reply is spoken aloud: write numbers, dates, times and units as words, avoid symbols and URLs. "
          "Jokes, riddles and stories should be about everyday topics (animals, food, family, travel, weather, school, nature, fairy-tale characters), not about programming, computers or technology unless the user asks. "
          "Always answer in German (Deutsch), whatever language the user's text looks like.",
    "fr": "You are Nils, a voice assistant. {length} No markdown, no emoji, no lists. "
          "Your reply is spoken aloud: write numbers, dates, times and units as words, avoid symbols and URLs. "
          "Jokes, riddles and stories should be about everyday topics (animals, food, family, travel, weather, school, nature, fairy-tale characters), not about programming, computers or technology unless the user asks. "
          "Always answer in French (français), whatever language the user's text looks like.",
}
LENGTH_RULE = {
    "ru": {"short": "Отвечай ОДНИМ-ДВУМЯ короткими предложениями, максимум {w} слов.",
           "medium": "Отвечай тремя-пятью предложениями, максимум {w} слов.",
           "long": "Отвечай развёрнуто, но структурированно: шесть-десять предложений, максимум {w} слов."},
    "en": {"short": "Reply in ONE or TWO short sentences, at most {w} words.",
           "medium": "Reply in three to five sentences, at most {w} words.",
           "long": "Reply in detail but in an organized way: six to ten sentences, at most {w} words."},
}
# Правила длины для es/de/fr — на английском (инструкции модели), язык ответа задан в BASE_PROMPT
for _l in ("es", "de", "fr"):
    LENGTH_RULE[_l] = LENGTH_RULE["en"]

# Стиль характера. Для русского — по-русски, для остальных языков — по-английски (модель отвечает на
# языке пользователя, который задан в BASE_PROMPT).
STYLE = {
    "ru": {
        "friendly": "Характер: дружелюбный. Говори тепло и доброжелательно, как хороший знакомый, обращайся на «ты».",
        "formal": "Характер: официальный. Говори вежливо, сдержанно и по делу, обращайся на «вы», без шуток и сленга.",
        "humorous": "Характер: юмористический. Добавляй уместные шутки и лёгкую иронию, но сначала давай полезный ответ.",
        "sarcastic": "Характер: саркастичный. Отвечай с сухой иронией и колкостью, но не грубо и не оскорбительно; "
                     "полезный ответ обязателен.",
        "motivating": "Характер: мотивирующий. Подбадривай, говори уверенно и вдохновляюще, настраивай на действие.",
        "calm": "Характер: спокойный. Говори размеренно, мягко и умиротворяюще, не торопись и не нагнетай.",
        "energetic": "Характер: энергичный. Говори бодро, живо и с энтузиазмом, короткими динамичными фразами.",
        "wise": "Характер: мудрый. Говори рассудительно, при случае вспоминай пословицу, притчу или меткую мысль.",
        "caring": "Характер: заботливый. Проявляй внимание к собеседнику, мягко напоминай об отдыхе и самочувствии.",
        "neutral": "Характер: нейтральный. Отвечай сухо, коротко и по делу, без эмоций, шуток и лишних слов.",
    },
    "en": {
        "friendly": "Personality: friendly. Speak warmly and kindly, like a good friend, informal address.",
        "formal": "Personality: formal. Be polite, reserved and businesslike, use formal address, no jokes or slang.",
        "humorous": "Personality: humorous. Add fitting jokes and light irony, but always give a useful answer first.",
        "sarcastic": "Personality: sarcastic. Answer with dry irony and wit, never rude or insulting; "
                     "the answer must still be useful.",
        "motivating": "Personality: motivating. Encourage the user, sound confident and inspiring, push toward action.",
        "calm": "Personality: calm. Speak in a measured, gentle, soothing way; never rush or create tension.",
        "energetic": "Personality: energetic. Sound upbeat, lively and enthusiastic, with short dynamic phrases.",
        "wise": "Personality: wise. Speak thoughtfully; when fitting, recall a proverb, parable or sharp insight.",
        "caring": "Personality: caring. Show attention to the user and gently remind them to rest and look after themselves.",
        "neutral": "Personality: neutral. Answer dryly, briefly and to the point, with no emotion, jokes or filler.",
    },
}
for _l in ("es", "de", "fr"):
    STYLE[_l] = STYLE["en"]


def build_system_prompt(device_settings: dict | None, user_text: str = "") -> tuple[str, int, str]:
    """Системный промпт для LLM = база языка + длина ответа + характер.
    Возвращает (промпт, num_predict, режим)."""
    n = normalize(device_settings)
    lang = n["language"]
    mode = choose_mode(user_text, lang)
    words, num_predict = MODES[mode]
    length = LENGTH_RULE[lang][mode].format(w=words)
    prompt = BASE_PROMPT[lang].format(length=length) + " " + STYLE[lang][n["character_id"]]
    return prompt, num_predict, mode


# ---------------------------------------------------------------- служебные фразы сервера
PHRASES = {
    "too_short": {"ru": "Слишком коротко, повторите, пожалуйста.", "en": "That was too short, please repeat.",
                  "es": "Demasiado corto, repítelo, por favor.", "de": "Das war zu kurz, bitte wiederholen.",
                  "fr": "C'était trop court, répétez s'il vous plaît."},
    "not_heard": {"ru": "Не расслышал, повторите, пожалуйста.", "en": "I didn't catch that, please repeat.",
                  "es": "No te he oído bien, repítelo, por favor.", "de": "Das habe ich nicht verstanden, bitte wiederholen.",
                  "fr": "Je n'ai pas bien entendu, répétez s'il vous plaît."},
    "error": {"ru": "Произошла ошибка, попробуйте ещё раз.", "en": "Something went wrong, please try again.",
              "es": "Ha ocurrido un error, inténtalo de nuevo.", "de": "Es ist ein Fehler aufgetreten, bitte erneut versuchen.",
              "fr": "Une erreur s'est produite, réessayez s'il vous plaît."},
    "llm_error": {"ru": "Извините, произошла ошибка при генерации ответа.",
                  "en": "Sorry, an error occurred while generating the answer.",
                  "es": "Lo siento, ocurrió un error al generar la respuesta.",
                  "de": "Entschuldigung, beim Erzeugen der Antwort ist ein Fehler aufgetreten.",
                  "fr": "Désolé, une erreur s'est produite lors de la génération de la réponse."},
    "llm_empty": {"ru": "Извините, я не смог сформулировать ответ.", "en": "Sorry, I couldn't come up with an answer.",
                  "es": "Lo siento, no he podido formular una respuesta.",
                  "de": "Entschuldigung, ich konnte keine Antwort formulieren.",
                  "fr": "Désolé, je n'ai pas pu formuler de réponse."},
}


def phrase(key: str, device_settings: dict | None = None) -> str:
    return PHRASES[key][normalize(device_settings)["language"]]


def fallbacks(device_settings: dict | None) -> dict:
    return {"llm_error": phrase("llm_error", device_settings), "llm_empty": phrase("llm_empty", device_settings)}


# ---------------------------------------------------------------- справочник для приложения/сайта
def catalog_payload(language: str = "ru", models_available: set | None = None) -> dict:
    from .catalog import (CHARACTER_INFO, LANGUAGE_NAMES, VOICE_VERIFIED, WAKE_WORDS)
    lang = language if language in LANGUAGES else DEFAULT_LANGUAGE
    return {
        "defaults": {"wake_word": "nils", "voice_gender": DEFAULT_VOICE_GENDER, "character_id": DEFAULT_CHARACTER,
                     "language": DEFAULT_LANGUAGE},
        "languages": [{"id": k, "name": v} for k, v in LANGUAGE_NAMES.items()],
        # model_ready: у встроенных слов модель есть всегда; у «Нильс»/своих — если модель загружена на сервер
        "wake_words": [{**w, "model_ready": w["engine"] == "builtin" or w["id"] in (models_available or set())}
                       for w in WAKE_WORDS],
        "voices": [{"gender": g, "speaker": VOICES[lang][g], "verified": VOICE_VERIFIED[lang]}
                   for g in ("male", "female")],
        "characters": [{"id": c, "name": CHARACTER_INFO[c]["name"][lang],
                        "description": CHARACTER_INFO[c]["description"][lang]} for c in CHARACTERS],
        "english_name": LANGUAGE_ENGLISH[lang],
    }


# ---------------------------------------------------------------- темы для шуток и историй
# Без подсказки модель выбирает самую «заезженную» шутку (про компьютеры и байты). Подсказка со случайной бытовой темой
# добавляется только к тексту запроса к модели (в историю и журнал она не попадает).
_CREATIVE = {
    "ru": re.compile(r"анекдот|шутк|пошути|шутку|смешн|рассмеш|истори|сказк|рассказ|басн|загадк|стишок|прикол", re.I),
    "en": re.compile(r"\bjoke|funny|\bstory|\btale\b|riddle|make me laugh|bedtime", re.I),
    "es": re.compile(r"chiste|broma|gracios|cuento|historia|adivinanza|hazme reír", re.I),
    "de": re.compile(r"witz|scherz|lustig|geschichte|märchen|rätsel|erzähl", re.I),
    "fr": re.compile(r"blague|drôle|histoire|conte|devinette|fais-moi rire", re.I),
}
_TOPICS = {
    "ru": ["кот и холодильник", "собака на прогулке", "бабушка и пирожки", "поход за грибами", "дождливая погода", "первый день в школе",
           "ёжик и яблоки", "рыбалка", "поезд и чемодан", "повар и суп", "маленький дракон", "зимний сад", "пингвин на пляже",
           "дедушка и огород", "утка и лужа", "магазин игрушек", "путешествие на корабле", "медведь и мёд", "волшебная лампа", "футбол во дворе"],
    "en": ["a cat and the fridge", "a dog on a walk", "grandma's pies", "a mushroom hunt", "rainy weather", "the first day of school",
           "a hedgehog and apples", "fishing", "a train and a suitcase", "a chef and soup", "a little dragon", "a penguin at the beach",
           "a bear and honey", "a magic lamp", "football in the yard", "a duck and a puddle", "a toy shop", "a sea voyage"],
    "es": ["un gato y la nevera", "un perro de paseo", "las empanadas de la abuela", "buscar setas", "un día de lluvia", "el primer día de colegio",
           "un erizo y manzanas", "la pesca", "un tren y una maleta", "un cocinero y la sopa", "un dragón pequeño", "un pingüino en la playa",
           "un oso y la miel", "una lámpara mágica", "fútbol en el patio", "un pato y un charco"],
    "de": ["eine Katze und der Kühlschrank", "ein Hund beim Spaziergang", "Omas Kuchen", "Pilze sammeln", "Regenwetter", "der erste Schultag",
           "ein Igel und Äpfel", "Angeln", "ein Zug und ein Koffer", "ein Koch und die Suppe", "ein kleiner Drache", "ein Pinguin am Strand",
           "ein Bär und Honig", "eine Wunderlampe", "Fußball im Hof", "eine Ente und eine Pfütze"],
    "fr": ["un chat et le frigo", "un chien en promenade", "les tartes de grand-mère", "la cueillette des champignons", "un jour de pluie",
           "le premier jour d'école", "un hérisson et des pommes", "la pêche", "un train et une valise", "un cuisinier et la soupe",
           "un petit dragon", "un pingouin à la plage", "un ours et le miel", "une lampe magique", "football dans la cour", "un canard et une flaque"],
}
_TOPIC_HINT = {
    "ru": " (Тема: {t}. Не про компьютеры и технологии.)",
    "en": " (Topic: {t}. Not about computers or technology.)",
    "es": " (Tema: {t}. No sobre ordenadores ni tecnología.)",
    "de": " (Thema: {t}. Nicht über Computer oder Technik.)",
    "fr": " (Thème : {t}. Pas sur les ordinateurs ni la technologie.)",
}


def creative_hint(user_text: str, language: str = "ru", rng=None) -> str:
    """Для просьб пошутить/рассказать историю — случайная бытовая тема; для остальных запросов пустая строка."""
    import random
    lang = language if language in _CREATIVE else "ru"
    if not _CREATIVE[lang].search(user_text or ""):
        return ""
    topic = (rng or random).choice(_TOPICS[lang])
    return _TOPIC_HINT[lang].format(t=topic)
