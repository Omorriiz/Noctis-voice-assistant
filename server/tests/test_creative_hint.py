"""Подсказка темы для шуток/историй: без неё модель выдаёт одну и ту же шутку про компьютеры."""
import random

import pytest

from noctis_server import personalization as pz

ASKS = [
    ("ru", "Расскажи анекдот"), ("ru", "пошути"), ("ru", "расскажи сказку на ночь"), ("ru", "Расскажи смешную историю"),
    ("en", "Tell me a joke"), ("en", "tell me a bedtime story"), ("es", "Cuéntame un chiste"), ("es", "cuéntame un cuento"),
    ("de", "Erzähl mir einen Witz"), ("de", "erzähl mir ein Märchen"), ("fr", "Raconte-moi une blague"), ("fr", "raconte une histoire"),
]
NOT_CREATIVE = [
    ("ru", "Который час?"), ("ru", "Какая сегодня погода"), ("ru", "Включи музыку"), ("ru", "Поставь таймер на пять минут"),
    ("en", "What time is it"), ("en", "Set a timer for five minutes"), ("es", "¿Qué hora es?"), ("de", "Wie spät ist es"), ("fr", "Quelle heure est-il"),
]
TECH_WORDS = ("компьют", "программ", "техно", "computer", "technolog", "ordenador", "tecnolog", "technik", "ordinateur", "байт")


@pytest.mark.parametrize("lang,text", ASKS)
def test_creative_requests_get_a_topic(lang, text):
    hint = pz.creative_hint(text, lang)
    assert hint.strip().startswith("(") and hint.strip().endswith(".)")


@pytest.mark.parametrize("lang,text", NOT_CREATIVE)
def test_other_requests_get_no_hint(lang, text):
    assert pz.creative_hint(text, lang) == ""


def test_topics_are_everyday_and_varied():
    for lang, topics in pz._TOPICS.items():
        assert len(set(topics)) >= 12
        for topic in topics:
            assert not any(w in topic.lower() for w in TECH_WORDS), (lang, topic)
    seen = {pz.creative_hint("Расскажи анекдот", "ru", random.Random(i)) for i in range(40)}
    assert len(seen) >= 8


def test_hint_goes_only_to_the_model_not_to_history():
    """ask_llm_stream кладёт в историю исходную фразу пользователя, а подсказку — только в запрос к модели."""
    from noctis_server.voice.assistant import NoctisAssistant

    sent = {}

    def fake_chat(**kw):
        sent["messages"] = kw["messages"]
        yield {"message": {"content": "Жил-был кот."}, "done": True, "done_reason": "stop"}

    import noctis_server.voice.pipeline as pipeline
    old = pipeline.chat
    pipeline.chat = fake_chat
    try:
        assistant = NoctisAssistant.__new__(NoctisAssistant)
        assistant.beam_size = 1
        from collections import deque
        history = deque(maxlen=6)
        list(NoctisAssistant.ask_llm_stream(assistant, "Расскажи анекдот", history,
                                            {"language": "ru", "character_id": "friendly", "voice_gender": "male"}))
    finally:
        pipeline.chat = old
    assert sent["messages"][-1]["content"].startswith("Расскажи анекдот (Тема:")
    assert history[0]["content"] == "Расскажи анекдот"
