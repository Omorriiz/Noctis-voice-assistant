"""Быстрый ответ: ранний первый кусок озвучки, замеры LLM, короткие реплики без LLM («привет», «спасибо», «повтори»)."""
import itertools
from collections import deque

import pytest

from noctis_server import intents, personalization as pz, skills, smalltalk
from noctis_server.catalog import LANGUAGES
from noctis_server.voice.pipeline import VoiceAssistant


# ------------------------------------------------------------------ первый кусок ответа
def test_first_chunk_starts_earlier_than_the_rest():
    split = VoiceAssistant._split_point
    assert split("Хорошо. Сейчас ", first=False) is None            # обычное правило: короткое предложение ждёт соседей
    assert split("Хорошо, сейчас сделаю. ", first=True) == len("Хорошо, сейчас сделаю. ")
    assert split("Конечно, сделаю! Сейчас ", first=True) == len("Конечно, сделаю! ")   # у самого первого куска порог ниже
    # без законченного предложения — по запятой, но только если кусок уже достаточно длинный
    assert split("Смотрите, это ", first=True) is None
    long_clause = "Это очень длинная первая часть ответа, а дальше"
    cut = split(long_clause, first=True)
    assert cut is not None and long_clause[:cut].rstrip().endswith(",")
    assert split("Курс составляет 3.5 доллара ", first=True) is None       # дробное число не режем


def test_split_does_not_change_later_sentences():
    split = VoiceAssistant._split_point
    assert split("Это первое длинное предложение для теста. Дальше ", first=False) == len("Это первое длинное предложение для теста. ")
    assert split("Да, но ", first=False) is None


# ------------------------------------------------------------------ замеры LLM и видеопамять
def test_llm_stats_are_computed_from_ollama_final_chunk():
    final = {"load_duration": 2_500_000_000, "prompt_eval_duration": 800_000_000, "prompt_eval_count": 120,
             "eval_count": 40, "eval_duration": 4_000_000_000}
    st = VoiceAssistant._llm_stats(final, t_request=0.0, t_first_token=3.4)
    assert st["load"] == 2.5 and st["prompt"] == 0.8 and st["tokens"] == 40
    assert st["tok_per_sec"] == 10.0 and st["first_token"] == 3.4 and st["prompt_tokens"] == 120
    assert VoiceAssistant._llm_stats({}, 0.0, None)["tok_per_sec"] == 0.0     # нет данных — не падаем


@pytest.mark.parametrize("size,vram,expected", [(8, 8, 1.0), (8, 6, 0.75), (8, 0, 0.0), (0, 0, 0.0)])
def test_gpu_fit_share(size, vram, expected):
    assert VoiceAssistant._gpu_fit(size, vram) == expected


def test_stream_records_timing_and_uses_early_first_chunk(monkeypatch):
    from noctis_server.voice import pipeline
    from noctis_server.voice.assistant import NoctisAssistant

    def fake_chat(**kw):
        yield {"message": {"content": "Конечно, я прямо сейчас "}, "done": False}
        yield {"message": {"content": "сделаю это, а потом "}, "done": False}
        yield {"message": {"content": "расскажу подробнее."}, "done": True, "done_reason": "stop",
               "eval_count": 12, "eval_duration": 1_000_000_000, "load_duration": 0, "prompt_eval_duration": 0, "prompt_eval_count": 30}

    monkeypatch.setattr(pipeline, "chat", fake_chat)
    a = NoctisAssistant(preload=False)
    out = list(a.ask_llm_stream("сделай это", deque(maxlen=10), {"language": "ru"}))
    assert out[0].endswith(",") and len(out) >= 2            # первый кусок ушёл по запятой, не дожидаясь конца предложения
    assert a.last_llm_stats["tokens"] == 12 and a.last_llm_stats["tok_per_sec"] == 12.0
    assert a.last_llm_stats["first_token"] is not None


# ------------------------------------------------------------------ короткие ответы
def test_prompts_ask_for_short_answers_without_preamble():
    for lang in LANGUAGES:
        for mode in ("short", "medium", "long"):
            words, num_predict = pz.MODES[mode]
            rule = pz.LENGTH_RULE[lang][mode].format(w=words)
            assert str(words) in rule
            assert ("без вступлений" in rule) or ("no preamble" in rule) or ("do not repeat" in rule)
    assert pz.MODES["short"][1] <= 70 and pz.MODES["medium"][1] <= 180 and pz.MODES["long"][1] <= 400


# ------------------------------------------------------------------ реплики без LLM
SAMPLES = [
    ("ru", "Привет!", "hello"), ("ru", "Привет, Нильс", "hello"), ("ru", "Доброе утро", "hello"),
    ("ru", "Спасибо большое", "thanks"), ("ru", "Пока", "bye"), ("ru", "Как дела?", "how"), ("ru", "Кто ты?", "who"),
    ("ru", "Как тебя зовут", "who"), ("ru", "Что ты умеешь?", "abilities"), ("ru", "Повтори, пожалуйста", "repeat"),
    ("en", "Hello!", "hello"), ("en", "Thank you very much", "thanks"), ("en", "Goodbye", "bye"), ("en", "What's up?", "how"),
    ("en", "Who are you?", "who"), ("en", "What can you do?", "abilities"), ("en", "Say that again", "repeat"),
    ("es", "Hola", "hello"), ("es", "Muchas gracias", "thanks"), ("es", "¿Quién eres?", "who"), ("es", "¿Qué puedes hacer?", "abilities"),
    ("es", "Repite eso", "repeat"), ("de", "Hallo", "hello"), ("de", "Vielen Dank", "thanks"), ("de", "Wie geht's?", "how"),
    ("de", "Wie heißt du?", "who"), ("de", "Was kannst du?", "abilities"), ("de", "Noch einmal", "repeat"),
    ("fr", "Bonjour", "hello"), ("fr", "Merci beaucoup", "thanks"), ("fr", "Ça va ?", "how"), ("fr", "Comment tu t'appelles ?", "who"),
    ("fr", "Que sais-tu faire ?", "abilities"), ("fr", "Répète", "repeat"),
]


@pytest.mark.parametrize("lang,text,kind", SAMPLES)
def test_smalltalk_is_recognised(lang, text, kind):
    it = intents.parse(text, lang)
    assert it is not None and it.kind == "smalltalk" and it.params["kind"] == kind


@pytest.mark.parametrize("lang,text", [
    ("ru", "Привет, расскажи про космос"), ("ru", "Спасибо за что так мало места в холодильнике"), ("ru", "Привет поставь таймер на пять минут"),
    ("ru", "Кто такой Пушкин"), ("ru", "Как дела у Пушкина в Михайловском"), ("en", "Hello, what is the capital of France"),
    ("en", "Who are you voting for"), ("de", "Hallo, wie spät ist es morgen in Tokio"), ("fr", "Merci de mettre un minuteur de dix minutes"),
])
def test_other_requests_are_not_intercepted(lang, text):
    it = intents.parse(text, lang)
    assert it is None or it.kind != "smalltalk"


def test_every_kind_has_replies_in_every_language():
    for kind, lang in itertools.product(smalltalk.REPLIES, LANGUAGES):
        options = smalltalk.REPLIES[kind][lang]
        assert options and all(o.strip() for o in options)
    for lang in LANGUAGES:
        for kind in smalltalk.PATTERNS[lang]:
            assert kind in smalltalk.REPLIES or kind == "repeat"       # «повтори» берёт ответ из истории
    # русские ответы читаются синтезатором: без цифр и латиницы
    for kind, options in smalltalk.REPLIES.items():
        for o in options["ru"]:
            assert not any(ch.isdigit() or ("a" <= ch.lower() <= "z") for ch in o), o


def test_repeat_says_the_last_answer_or_that_there_is_none():
    class Ctx:
        lang = "ru"
        history = deque([{"role": "user", "content": "что нового"}, {"role": "assistant", "content": "Нового ничего нет."},
                         {"role": "user", "content": "повтори"}])

    assert skills._smalltalk(intents.Intent("smalltalk", {"kind": "repeat"}), Ctx()).text == "Нового ничего нет."
    Ctx.history = deque()
    assert skills._smalltalk(intents.Intent("smalltalk", {"kind": "repeat"}), Ctx()).text in smalltalk.REPLIES["repeat_none"]["ru"]


def test_voice_smalltalk_skips_the_llm(client, auth, assistant):
    from .helpers import open_ws, paired, reply_text, say
    pid, token = paired(client, auth)
    ctx, ws, _ = open_ws(client, pid, token)
    try:
        text = reply_text(say(ws, assistant, "Привет!"))
        assert text in smalltalk.REPLIES["hello"]["ru"]
        assert not [c for c in assistant.calls if c[0] == "llm"]
    finally:
        ctx.__exit__(None, None, None)
