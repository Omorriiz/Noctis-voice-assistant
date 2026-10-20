"""Справочники персонализации: допустимые значения, названия и описания на 5 языках."""
import re

LANGUAGES = ("ru", "en", "es", "de", "fr")
LANGUAGE_NAMES = {"ru": "Русский", "en": "English", "es": "Español", "de": "Deutsch", "fr": "Français"}
# Название языка для инструкции модели («Answer in …»)
LANGUAGE_ENGLISH = {"ru": "Russian", "en": "English", "es": "Spanish", "de": "German", "fr": "French"}
THEMES = ("light", "dark", "system")
VOICE_GENDERS = ("male", "female")
CHARACTERS = ("friendly", "formal", "humorous", "sarcastic", "motivating",
              "calm", "energetic", "wise", "caring", "neutral")
DEFAULT_WAKE_WORD = "nils"
DEFAULT_CHARACTER = "friendly"
DEFAULT_VOICE_GENDER = "male"

# ---------------------------------------------------------------- слова пробуждения
# Пользователю доступны ровно два слова: «Нильс» (модель microWakeWord, этап 4) и «Alexa» (готовая модель Espressif wn10_alexa).
# Остальные пресеты прошивки (Привет ESP, Hi ESP, Computer, Jarvis…) и свои фразы (`custom:`) пользователю не предлагаются и сервером
# не принимаются; прошивка их по-прежнему понимает (раздел model), но сервер их не присылает.
# engine: "builtin" — готовая модель Espressif (переключается без перепрошивки приложения),
#         "custom_model" — модель microWakeWord «Нильс».
WAKE_WORDS = [
    {"id": "nils", "label": "Нильс / Nils", "language": "ru", "engine": "custom_model", "default": True},
    {"id": "alexa", "label": "Alexa", "language": "en", "engine": "builtin"},
]
WAKE_WORD_IDS = tuple(w["id"] for w in WAKE_WORDS)


def is_valid_wake_word(value: str) -> bool:
    """Только слова из WAKE_WORDS («nils», «alexa»)."""
    return value in WAKE_WORD_IDS


def safe_wake_word(value: str | None) -> str:
    """Слово из базы, которое сервера больше не предлагает (старые данные), заменяется словом по умолчанию."""
    return value if value in WAKE_WORD_IDS else DEFAULT_WAKE_WORD


# ---------------------------------------------------------------- голоса
# Русский: Silero v5_ru. Мужской — aidar (по умолчанию), женский — xenia.
# Остальные языки: Silero v3 (отдельная модель на язык). ВНИМАНИЕ: у голосов v3 нет метки пола в
# метаданных; сопоставление ниже — стартовое, его нужно проверить на слух через предпрослушивание
# (POST /api/v1/personalization/voice-preview) и при необходимости поправить здесь.
SILERO_MODELS = {  # язык -> (language, speaker-id модели для torch.hub)
    "ru": ("ru", "v5_ru"), "en": ("en", "v3_en"), "es": ("es", "v3_es"),
    "de": ("de", "v3_de"), "fr": ("fr", "v3_fr"),
}
VOICES = {
    "ru": {"male": "aidar", "female": "xenia"},
    "en": {"male": "en_0", "female": "en_1"},
    "es": {"male": "es_0", "female": "es_1"},
    "de": {"male": "karlsson", "female": "eva_k"},
    "fr": {"male": "fr_0", "female": "fr_1"},
}
VOICE_VERIFIED = {"ru": True, "en": False, "es": False, "de": False, "fr": False}

# ---------------------------------------------------------------- характеры
# name / description по языкам; sample — пример реплики (предпрослушивание характера)
CHARACTER_INFO = {
    "friendly": {
        "name": {"ru": "Дружелюбный", "en": "Friendly", "es": "Amistoso", "de": "Freundlich", "fr": "Amical"},
        "description": {
            "ru": "Тёплый и доброжелательный, как хороший знакомый.",
            "en": "Warm and kind, like a good friend.",
            "es": "Cálido y amable, como un buen amigo.",
            "de": "Warmherzig und nett, wie ein guter Freund.",
            "fr": "Chaleureux et bienveillant, comme un bon ami."},
        "sample": {
            "ru": "Привет! Конечно, помогу. Сегодня отличный день, чтобы сделать что-то хорошее!",
            "en": "Hi! Of course, I'd be happy to help. It's a great day to do something nice!",
            "es": "¡Hola! Claro que te ayudo. ¡Es un gran día para hacer algo bueno!",
            "de": "Hallo! Natürlich helfe ich dir gern. Heute ist ein toller Tag!",
            "fr": "Salut ! Bien sûr, je t'aide avec plaisir. C'est une belle journée !"}},
    "formal": {
        "name": {"ru": "Официальный", "en": "Formal", "es": "Formal", "de": "Förmlich", "fr": "Formel"},
        "description": {
            "ru": "Вежливый, сдержанный, обращается на «вы».",
            "en": "Polite and reserved, strictly professional.",
            "es": "Educado y reservado, estrictamente profesional.",
            "de": "Höflich und zurückhaltend, streng professionell.",
            "fr": "Poli et réservé, strictement professionnel."},
        "sample": {
            "ru": "Добрый день. К вашим услугам. Чем могу быть полезен?",
            "en": "Good afternoon. At your service. How may I assist you?",
            "es": "Buenas tardes. A su servicio. ¿En qué puedo ayudarle?",
            "de": "Guten Tag. Zu Ihren Diensten. Wie kann ich Ihnen behilflich sein?",
            "fr": "Bonjour. À votre service. En quoi puis-je vous être utile ?"}},
    "humorous": {
        "name": {"ru": "Юмористический", "en": "Humorous", "es": "Divertido", "de": "Humorvoll", "fr": "Humoristique"},
        "description": {
            "ru": "Шутит к месту, отвечает с лёгкой иронией.",
            "en": "Jokes around and answers with a light touch.",
            "es": "Bromea y responde con un toque de humor.",
            "de": "Macht Witze und antwortet mit Augenzwinkern.",
            "fr": "Plaisante et répond avec une touche d'humour."},
        "sample": {
            "ru": "Погода отличная, если вы утка. А если нет, то зонтик не помешает!",
            "en": "The weather is great, if you're a duck. If not, take an umbrella!",
            "es": "El tiempo es perfecto, si eres un pato. Si no, ¡lleva paraguas!",
            "de": "Das Wetter ist super, wenn du eine Ente bist. Sonst nimm einen Schirm!",
            "fr": "Il fait super beau, si vous êtes un canard. Sinon, prenez un parapluie !"}},
    "sarcastic": {
        "name": {"ru": "Саркастичный", "en": "Sarcastic", "es": "Sarcástico", "de": "Sarkastisch", "fr": "Sarcastique"},
        "description": {
            "ru": "Колкий и ироничный, но без грубости.",
            "en": "Dry and ironic, but never rude.",
            "es": "Seco e irónico, pero nunca grosero.",
            "de": "Trocken und ironisch, aber nie unhöflich.",
            "fr": "Pince-sans-rire et ironique, mais jamais grossier."},
        "sample": {
            "ru": "О, ещё один вопрос о времени. Какая неожиданность. Сейчас как раз самое время.",
            "en": "Oh, another question about the time. What a surprise. It's, shockingly, right now.",
            "es": "Ah, otra pregunta sobre la hora. Qué sorpresa. Es, increíblemente, ahora mismo.",
            "de": "Oh, noch eine Frage nach der Uhrzeit. Was für eine Überraschung. Es ist, tatsächlich, jetzt.",
            "fr": "Oh, encore une question sur l'heure. Quelle surprise. Il est, étonnamment, maintenant."}},
    "motivating": {
        "name": {"ru": "Мотивирующий", "en": "Motivating", "es": "Motivador", "de": "Motivierend", "fr": "Motivant"},
        "description": {
            "ru": "Подбадривает и настраивает на результат.",
            "en": "Encourages you and keeps you on track.",
            "es": "Te anima y te mantiene enfocado.",
            "de": "Ermutigt dich und hält dich auf Kurs.",
            "fr": "Vous encourage et vous garde sur la bonne voie."},
        "sample": {
            "ru": "Ты справишься! Один шаг сегодня — и цель станет ближе. Вперёд!",
            "en": "You can do it! One step today brings your goal closer. Let's go!",
            "es": "¡Tú puedes! Un paso hoy y tu meta estará más cerca. ¡Adelante!",
            "de": "Du schaffst das! Ein Schritt heute bringt dich dem Ziel näher. Los geht's!",
            "fr": "Tu en es capable ! Un pas aujourd'hui rapproche ton objectif. Allez !"}},
    "calm": {
        "name": {"ru": "Спокойный", "en": "Calm", "es": "Tranquilo", "de": "Ruhig", "fr": "Calme"},
        "description": {
            "ru": "Размеренный и умиротворяющий.",
            "en": "Measured and soothing.",
            "es": "Pausado y relajante.",
            "de": "Gelassen und beruhigend.",
            "fr": "Posé et apaisant."},
        "sample": {
            "ru": "Не спешите. Сделайте вдох. Всё получится, мы разберёмся не торопясь.",
            "en": "Take your time. Breathe in. Everything will work out, one step at a time.",
            "es": "Sin prisa. Respira hondo. Todo saldrá bien, paso a paso.",
            "de": "Keine Eile. Atme tief durch. Alles wird gut, Schritt für Schritt.",
            "fr": "Prenez votre temps. Respirez. Tout ira bien, pas à pas."}},
    "energetic": {
        "name": {"ru": "Энергичный", "en": "Energetic", "es": "Enérgico", "de": "Energiegeladen", "fr": "Énergique"},
        "description": {
            "ru": "Бодрый, быстрый, полный энтузиазма.",
            "en": "Upbeat, quick and full of enthusiasm.",
            "es": "Animado, rápido y lleno de entusiasmo.",
            "de": "Schwungvoll, schnell und voller Begeisterung.",
            "fr": "Dynamique, rapide et plein d'enthousiasme."},
        "sample": {
            "ru": "Отлично, поехали! Уже делаю, это будет круто!",
            "en": "Awesome, let's go! On it right now, this is going to be great!",
            "es": "¡Genial, vamos! Ya lo hago, ¡va a ser increíble!",
            "de": "Super, los geht's! Mache ich sofort, das wird großartig!",
            "fr": "Super, c'est parti ! Je m'en occupe, ça va être génial !"}},
    "wise": {
        "name": {"ru": "Мудрый", "en": "Wise", "es": "Sabio", "de": "Weise", "fr": "Sage"},
        "description": {
            "ru": "Рассудительный, любит притчи и меткие мысли.",
            "en": "Thoughtful, fond of parables and sharp insight.",
            "es": "Reflexivo, aficionado a las parábolas y las ideas certeras.",
            "de": "Bedächtig, liebt Gleichnisse und treffende Gedanken.",
            "fr": "Réfléchi, aime les paraboles et les pensées justes."},
        "sample": {
            "ru": "Как говорится, путь в тысячу миль начинается с первого шага. Давайте начнём.",
            "en": "As they say, a journey of a thousand miles begins with a single step. Let us begin.",
            "es": "Como dicen, un viaje de mil millas comienza con un solo paso. Comencemos.",
            "de": "Wie man sagt: Eine Reise von tausend Meilen beginnt mit einem Schritt. Fangen wir an.",
            "fr": "Comme on dit, un voyage de mille lieues commence par un pas. Commençons."}},
    "caring": {
        "name": {"ru": "Заботливый", "en": "Caring", "es": "Atento", "de": "Fürsorglich", "fr": "Attentionné"},
        "description": {
            "ru": "Внимателен к вам, напоминает отдохнуть и поесть.",
            "en": "Attentive to you, reminds you to rest and eat.",
            "es": "Pendiente de ti, te recuerda descansar y comer.",
            "de": "Aufmerksam, erinnert dich an Pausen und Essen.",
            "fr": "Attentif, vous rappelle de vous reposer et de manger."},
        "sample": {
            "ru": "Конечно. Только не забудьте пообедать и немного отдохнуть, ладно?",
            "en": "Of course. Just don't forget to have lunch and rest a little, okay?",
            "es": "Por supuesto. Solo no olvides comer y descansar un poco, ¿vale?",
            "de": "Natürlich. Vergiss nur nicht, etwas zu essen und dich auszuruhen, ja?",
            "fr": "Bien sûr. N'oubliez pas de déjeuner et de vous reposer un peu, d'accord ?"}},
    "neutral": {
        "name": {"ru": "Нейтральный", "en": "Neutral", "es": "Neutral", "de": "Neutral", "fr": "Neutre"},
        "description": {
            "ru": "Коротко, по делу, без эмоций.",
            "en": "Short and to the point, no emotion.",
            "es": "Breve y directo, sin emociones.",
            "de": "Kurz und sachlich, ohne Emotionen.",
            "fr": "Bref et direct, sans émotion."},
        "sample": {
            "ru": "Сейчас четырнадцать часов тридцать минут.",
            "en": "It is two thirty in the afternoon.",
            "es": "Son las dos y media de la tarde.",
            "de": "Es ist vierzehn Uhr dreißig.",
            "fr": "Il est quatorze heures trente."}},
}
