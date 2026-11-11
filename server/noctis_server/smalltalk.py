"""Короткие бытовые реплики без LLM: приветствие, «спасибо», прощание, «как дела», «кто ты», «что ты умеешь», «повтори».

Ответ мгновенный (языковой модели нужны секунды). Правила узкие: реплика должна состоять ТОЛЬКО из такой фразы
(можно с именем «Нильс» и словом «пожалуйста»), поэтому «привет, расскажи про космос» уходит в LLM как обычно.
Шаблоны записаны для нормализованного текста (numwords.normalize): нижний регистр, без знаков препинания,
у es/de/fr без диакритики.
"""
import random
import re

NAME = r"(?:\s+(?:nils|нильс|ноктис|noctis|bitte|please|por favor|s il te plait|s il vous plait|пожалуйста))*"

PATTERNS = {
    "ru": {
        "hello": r"(привет|приветик|здравствуй|здравствуйте|добрый день|добрый вечер|доброе утро|доброй ночи|хай|хэй|эй)",
        "thanks": r"(спасибо|благодарю|спс|большое спасибо|спасибо большое|огромное спасибо|спасибо тебе|спасибо вам)",
        "bye": r"(пока|пока пока|до свидания|до встречи|до завтра|прощай|покеда|спокойной ночи)",
        "how": r"(как дела|как у тебя дела|как твои дела|как ты|как жизнь|как поживаешь|как настроение|как ты поживаешь)",
        "who": r"(кто ты|ты кто|как тебя зовут|как твое имя|как тебя звать|представься)",
        "abilities": r"(что ты умеешь|что ты можешь|что умеешь|что можешь|какие у тебя команды|чем ты можешь помочь|помощь|помоги мне)",
        "repeat": r"(повтори|повтори еще раз|повтори это|повтори еще|что ты сказал|что ты сказала|еще раз|не расслышал|не расслышала)",
    },
    "en": {
        "hello": r"(hello|hi|hey|hi there|hey there|good morning|good evening|good afternoon|good night)",
        "thanks": r"(thanks|thank you|thanks a lot|thank you very much|thank you so much|many thanks|cheers)",
        "bye": r"(bye|goodbye|bye bye|see you|see you later|see you tomorrow|good night|talk to you later)",
        "how": r"(how are you|how are you doing|how is it going|how do you do|how are things|what s up|whats up)",
        "who": r"(who are you|what is your name|what s your name|whats your name|introduce yourself|what are you)",
        "abilities": r"(what can you do|what do you do|what are your commands|how can you help|help|help me)",
        "repeat": r"(repeat|repeat that|say that again|say it again|come again|what did you say|repeat it|again|pardon)",
    },
    "es": {
        "hello": r"(hola|buenos dias|buenas tardes|buenas noches|buenas|hey)",
        "thanks": r"(gracias|muchas gracias|mil gracias|te lo agradezco)",
        "bye": r"(adios|hasta luego|hasta manana|hasta pronto|chao|nos vemos|buenas noches)",
        "how": r"(como estas|que tal|como te va|como andas|como va todo|que tal estas)",
        "who": r"(quien eres|como te llamas|cual es tu nombre|presentate|que eres)",
        "abilities": r"(que puedes hacer|que sabes hacer|que haces|cuales son tus comandos|en que puedes ayudar|ayuda|ayudame)",
        "repeat": r"(repite|repite eso|repitelo|repite otra vez|otra vez|que has dicho|que dijiste|como dices)",
    },
    "de": {
        "hello": r"(hallo|hi|hey|guten tag|guten morgen|guten abend|moin|servus|gute nacht)",
        "thanks": r"(danke|danke schon|vielen dank|dankeschon|besten dank|danke dir)",
        "bye": r"(tschuss|auf wiedersehen|bis spater|bis morgen|ciao|bis bald|gute nacht)",
        "how": r"(wie geht s|wie geht es dir|wie geht es ihnen|wie gehts|was geht|alles gut)",
        "who": r"(wer bist du|wie heisst du|wie ist dein name|stell dich vor|was bist du)",
        "abilities": r"(was kannst du|was kannst du alles|was machst du|welche befehle gibt es|wie kannst du helfen|hilfe|hilf mir)",
        "repeat": r"(wiederhole|wiederhole das|noch einmal|noch mal|nochmal|was hast du gesagt|bitte wiederholen)",
    },
    "fr": {
        "hello": r"(bonjour|salut|bonsoir|coucou|hello|hey|bonne nuit)",
        "thanks": r"(merci|merci beaucoup|merci bien|un grand merci|je te remercie)",
        "bye": r"(au revoir|salut|a bientot|a demain|a plus|adieu|bonne nuit|ciao)",
        "how": r"(comment vas tu|comment allez vous|ca va|comment ca va|tu vas bien|quoi de neuf)",
        "who": r"(qui es tu|comment tu t appelles|comment t appelles tu|quel est ton nom|presente toi|tu es qui)",
        "abilities": r"(que sais tu faire|que peux tu faire|qu est ce que tu sais faire|quelles sont tes commandes|aide|aide moi)",
        "repeat": r"(repete|repete ca|repete encore|encore une fois|qu as tu dit|qu est ce que tu as dit|pardon)",
    },
}
_RX = {lang: {kind: re.compile(r"^" + pat + NAME + r"$") for kind, pat in kinds.items()} for lang, kinds in PATTERNS.items()}

# «Привет» из «Привет, ESP» и т. п. не трогаем: правило срабатывает только на всю фразу целиком.
# Порядок проверки важен для пересечений (например, «salut» — и приветствие, и прощание: считаем приветствием).
ORDER = ("repeat", "abilities", "who", "how", "thanks", "hello", "bye")


def detect(normalized_text: str, lang: str) -> str | None:
    """Вид реплики (hello/thanks/bye/how/who/abilities/repeat) или None."""
    rx = _RX.get(lang)
    text = re.sub(r"\s+", " ", re.sub(r"['’`]", " ", normalized_text or "")).strip()   # «what's» → «what s», «t'appelles» → «t appelles»
    if not rx or not text or len(text.split()) > 6:
        return None
    for kind in ORDER:
        if rx[kind].match(text):
            return kind
    return None


def L(ru, en, es, de, fr):
    return {"ru": ru, "en": en, "es": es, "de": de, "fr": fr}


# Несколько вариантов ответа, чтобы колонка не повторялась слово в слово. Без родовых окончаний («рад/рада»):
# голос колонки мужской или женский по настройке.
REPLIES = {
    "hello": L(
        ["Здравствуйте! Чем могу помочь?", "Привет! Я вас слушаю.", "Привет! Что сделать?"],
        ["Hello! How can I help?", "Hi! I'm listening.", "Hey! What can I do for you?"],
        ["¡Hola! ¿En qué puedo ayudarte?", "¡Hola! Te escucho.", "¡Hola! ¿Qué hago por ti?"],
        ["Hallo! Wie kann ich helfen?", "Hallo! Ich höre zu.", "Hi! Was kann ich für dich tun?"],
        ["Bonjour ! Comment puis-je vous aider ?", "Salut ! Je vous écoute.", "Bonjour ! Que puis-je faire pour vous ?"]),
    "thanks": L(
        ["Пожалуйста!", "Обращайтесь!", "Не за что!"],
        ["You're welcome!", "Anytime!", "No problem!"],
        ["¡De nada!", "¡Un placer ayudar!", "¡No hay de qué!"],
        ["Gern geschehen!", "Bitte schön!", "Kein Problem!"],
        ["De rien !", "Avec plaisir !", "Pas de souci !"]),
    "bye": L(
        ["До свидания!", "Пока! Буду на связи.", "До встречи!"],
        ["Goodbye!", "Bye! I'll be here.", "See you soon!"],
        ["¡Adiós!", "¡Hasta pronto!", "¡Nos vemos!"],
        ["Auf Wiedersehen!", "Tschüss! Bis bald.", "Bis später!"],
        ["Au revoir !", "À bientôt !", "À plus tard !"]),
    "how": L(
        ["Отлично, спасибо! Чем могу помочь?", "Всё хорошо, спасибо, что спросили!", "Хорошо, спасибо! А у вас как дела?"],
        ["I'm great, thanks! How can I help?", "All good, thanks for asking!", "Doing well, thanks! And you?"],
        ["¡Muy bien, gracias! ¿En qué puedo ayudarte?", "Todo bien, ¡gracias por preguntar!", "Bien, gracias. ¿Y tú?"],
        ["Mir geht es gut, danke! Wie kann ich helfen?", "Alles gut, danke der Nachfrage!", "Gut, danke! Und dir?"],
        ["Très bien, merci ! Comment puis-je vous aider ?", "Tout va bien, merci de demander !", "Bien, merci. Et vous ?"]),
    "who": L(
        ["Я Нильс, голосовой ассистент платформы Ноктис. Могу сказать время, поставить таймер или будильник, включить музыку и рассказать о погоде."],
        ["I'm Nils, the voice assistant of the Noctis platform. I can tell the time, set timers and alarms, play music and give the weather."],
        ["Soy Nils, el asistente de voz de la plataforma Noctis. Puedo decir la hora, poner temporizadores y alarmas, reproducir música y dar el tiempo."],
        ["Ich bin Nils, der Sprachassistent der Plattform Noctis. Ich sage die Uhrzeit, stelle Timer und Wecker, spiele Musik und nenne das Wetter."],
        ["Je suis Nils, l'assistant vocal de la plateforme Noctis. Je donne l'heure, règle des minuteurs et des réveils, joue de la musique et annonce la météo."]),
    "abilities": L(
        ["Я говорю время и дату, ставлю таймеры и будильники, включаю музыку из вашей библиотеки, из каталога Джамендо и интернет-радио, "
         "меняю громкость, сообщаю погоду на сегодня и отвечаю на вопросы."],
        ["I tell the time and date, set timers and alarms, play music from your library, from the Jamendo catalog and internet radio, "
         "change the volume, give today's weather and answer questions."],
        ["Digo la hora y la fecha, pongo temporizadores y alarmas, reproduzco música de tu biblioteca, del catálogo de Jamendo y de radios por internet, "
         "cambio el volumen, doy el tiempo de hoy y respondo preguntas."],
        ["Ich sage Uhrzeit und Datum, stelle Timer und Wecker, spiele Musik aus deiner Bibliothek, aus dem Jamendo-Katalog und Internetradio, "
         "ändere die Lautstärke, nenne das heutige Wetter und beantworte Fragen."],
        ["Je donne l'heure et la date, je règle des minuteurs et des réveils, je joue de la musique de votre bibliothèque, du catalogue Jamendo et de la radio, "
         "je change le volume, j'annonce la météo du jour et je réponds à vos questions."]),
    "repeat_none": L(["Мне пока нечего повторить."], ["I have nothing to repeat yet."], ["Todavía no tengo nada que repetir."],
                     ["Ich habe noch nichts zu wiederholen."], ["Je n'ai encore rien à répéter."]),
}


def reply(kind: str, lang: str, rng=None) -> str:
    options = REPLIES[kind][lang]
    return (rng or random).choice(options)
