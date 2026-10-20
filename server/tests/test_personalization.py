"""Этап 3: персонализация — справочники, промпты, голос, язык, API и применение на конвейере."""
import itertools
import sys
import wave
import io
from pathlib import Path

import pytest

from noctis_server import personalization as pz
from noctis_server.catalog import (CHARACTER_INFO, CHARACTERS, LANGUAGES, VOICES, WAKE_WORDS,
                                   is_valid_wake_word)

from .conftest import register

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def hdr(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# ---------------------------------------------------------------- чистая логика
def test_defaults_are_spec_defaults():
    n = pz.normalize(None)
    assert n == {"language": "ru", "voice_gender": "male", "character_id": "friendly"}
    assert pz.voice_for(None) == ("ru", "aidar")  # мужской по умолчанию


def test_normalize_repairs_garbage():
    assert pz.normalize({"language": "xx", "voice_gender": "robot", "character_id": "evil"}) == pz.normalize({})


def test_voice_mapping_covers_every_language_and_gender():
    assert pz.voice_for({"language": "ru", "voice_gender": "female"}) == ("ru", "xenia")
    for lang, gender in itertools.product(LANGUAGES, ("male", "female")):
        language, speaker = pz.voice_for({"language": lang, "voice_gender": gender})
        assert language == lang and speaker == VOICES[lang][gender]
    for lang in LANGUAGES:  # мужской и женский — разные голоса
        assert VOICES[lang]["male"] != VOICES[lang]["female"]


@pytest.mark.parametrize("lang,char", list(itertools.product(LANGUAGES, CHARACTERS)))
def test_prompt_for_every_character_and_language(lang, char):
    prompt, num_predict, mode = pz.build_system_prompt({"language": lang, "character_id": char}, "hola")
    assert mode == "short" and num_predict == 80
    assert pz.STYLE[lang][char] in prompt            # характер попал в промпт
    assert "Nils" in prompt or "Нильс" in prompt
    assert "{" not in prompt and "}" not in prompt   # все подстановки выполнены


def test_characters_produce_different_prompts():
    prompts = {c: pz.build_system_prompt({"language": "en", "character_id": c})[0] for c in CHARACTERS}
    assert len(set(prompts.values())) == 10
    assert "formal" in prompts["formal"].lower() and "sarcastic" in prompts["sarcastic"].lower()


def test_prompt_demands_answer_language():
    assert "Always answer in German" in pz.build_system_prompt({"language": "de"})[0]
    assert "русскими буквами" in pz.build_system_prompt({"language": "ru"})[0]


@pytest.mark.parametrize("lang,text,mode", [
    ("ru", "расскажи подробно про космос", "long"), ("ru", "что такое чёрная дыра", "medium"), ("ru", "привет", "short"),
    ("en", "explain how engines work", "long"), ("en", "what is a planet", "medium"),
    ("es", "cuéntame una historia", "long"), ("de", "was ist ein Atom", "medium"),
    ("fr", "pourquoi le ciel est bleu", "long"), ("fr", "salut", "short")])
def test_length_mode_per_language(lang, text, mode):
    assert pz.choose_mode(text, lang) == mode


def test_phrases_exist_in_all_languages():
    for key, by_lang in pz.PHRASES.items():
        assert set(by_lang) == set(LANGUAGES), key
        assert all(by_lang.values())
    assert pz.phrase("error", {"language": "fr"}).startswith("Une erreur")
    assert pz.phrase("too_short") == "Слишком коротко, повторите, пожалуйста."


def test_catalog_texts_complete():
    for c in CHARACTERS:
        for field in ("name", "description", "sample"):
            assert set(CHARACTER_INFO[c][field]) == set(LANGUAGES), (c, field)
    assert len(CHARACTERS) == 10


def test_wake_word_validation():
    """Пользователю доступны ровно два слова: «Нильс» и «Alexa»."""
    assert [w["id"] for w in WAKE_WORDS] == ["nils", "alexa"]
    for ok in ("nils", "alexa"):
        assert is_valid_wake_word(ok), ok
    for bad in ("", "siri", "NILS", "Alexa", "privet_esp", "hi_esp", "hallo_esp", "hola_esp", "bonjour_esp", "computer", "jarvis",
                "custom:", "custom:Hey Noctis", "custom:Ноктис", "custom:Jean-Luc"):
        assert not is_valid_wake_word(bad), bad
    assert sum(1 for w in WAKE_WORDS if w.get("default")) == 1 and next(w for w in WAKE_WORDS if w.get("default"))["id"] == "nils"


def test_stored_removed_word_is_replaced_by_default():
    from noctis_server.catalog import safe_wake_word
    assert [safe_wake_word(w) for w in ("nils", "alexa", "jarvis", "custom:Ноктис", "", None)] == \
           ["nils", "alexa", "nils", "nils", "nils", "nils"]


# ---------------------------------------------------------------- паритет с прошивкой (настоящий Си-код)
def test_wake_word_rules_match_firmware(tmp_path_factory):
    from tools.virtual_speaker import Core, build_core_library
    try:
        core = Core(build_core_library(tmp_path_factory.mktemp("lib")))
    except Exception as e:
        pytest.skip(f"нет компилятора: {e}")
    samples = [w["id"] for w in WAKE_WORDS] + [
        "", "siri", "NILS", "custom:", "custom:a", "custom:ab", "custom:Hey Noctis", "custom:Ноктис", "custom:Jean-Luc",
        "custom:ab1", "custom: ab", "custom:ab ", "custom:-ab", "custom:a_b", "custom:a&b", "custom:123",
        "custom:" + "a" * 32, "custom:" + "a" * 33, "Custom:abc", "computer", "jarvis", "privet_esp"]
    for s in samples:
        # сервер строже прошивки (она понимает и другие пресеты); всё, что принимает сервер, прошивка обязана понимать
        if is_valid_wake_word(s):
            assert core.valid_wake_word(s), s


# ---------------------------------------------------------------- API
def test_catalog_endpoint_localised(client):
    ru = client.get("/api/v1/personalization/catalog").json()
    assert ru["defaults"] == {"wake_word": "nils", "voice_gender": "male", "character_id": "friendly", "language": "ru"}
    assert ru["characters"][0]["name"] == "Дружелюбный" and len(ru["characters"]) == 10
    assert [v["speaker"] for v in ru["voices"]] == ["aidar", "xenia"] and ru["voices"][0]["verified"] is True
    assert any(w["id"] == "nils" and w.get("default") for w in ru["wake_words"])
    de = client.get("/api/v1/personalization/catalog?language=de").json()
    assert de["characters"][1]["name"] == "Förmlich"
    assert client.get("/api/v1/personalization/catalog?language=it").status_code == 422


def test_character_sample(client):
    r = client.get("/api/v1/personalization/characters/sarcastic/sample?language=en").json()
    assert r["name"] == "Sarcastic" and r["sample"]
    assert client.get("/api/v1/personalization/characters/evil/sample").status_code == 404


def test_wake_word_validated_in_api(client, auth):
    assert client.patch("/api/v1/me/settings", json={"wake_word": "alexa"}, headers=auth).status_code == 200
    for bad in ("custom:Ноктис", "siri", "jarvis", "computer", "privet_esp"):
        assert client.patch("/api/v1/me/settings", json={"wake_word": bad}, headers=auth).status_code == 422, bad
    pid = client.post("/api/v1/devices", json={}, headers=auth).json()["public_id"]
    assert client.patch(f"/api/v1/devices/{pid}/settings", json={"wake_word": "bad word!"}, headers=auth).status_code == 422
    assert client.patch(f"/api/v1/devices/{pid}/settings", json={"wake_word": "alexa"}, headers=auth).status_code == 200
    assert client.patch(f"/api/v1/devices/{pid}/settings", json={"wake_word": "jarvis"}, headers=auth).status_code == 422


def test_voice_preview_returns_wav_in_selected_voice(client, auth, assistant):
    assistant.calls.clear()
    r = client.post("/api/v1/personalization/voice-preview", json={"language": "de", "voice_gender": "female"}, headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getnframes() == 3000
    tts = [c for c in assistant.calls if c[0] == "tts"]
    assert tts and tts[0][1] == CHARACTER_INFO["friendly"]["sample"]["de"]  # образец дружелюбного характера, немецкий
    r = client.post("/api/v1/personalization/voice-preview",
                    json={"language": "en", "voice_gender": "male", "character_id": "neutral"}, headers=auth)
    assert r.status_code == 200
    assert client.post("/api/v1/personalization/voice-preview", json={"language": "it"}, headers=auth).status_code == 422
    assert client.post("/api/v1/personalization/voice-preview", json={"text": "x" * 301}, headers=auth).status_code == 422
    assert client.post("/api/v1/personalization/voice-preview", json={}).status_code == 401


def test_apply_profile_to_all_devices_and_push(client, auth):
    d1 = client.post("/api/v1/devices", json={}, headers=auth).json()["public_id"]
    d2 = client.post("/api/v1/devices", json={}, headers=auth).json()
    client.patch("/api/v1/me/settings", json={"voice_gender": "female", "character_id": "wise", "language": "fr",
                                              "wake_word": "alexa"}, headers=auth)
    with client.websocket_connect("/noctis/v1/", headers={"X-Device-Id": d2["public_id"], "X-Device-Token": d2["device_token"]}) as ws:
        ws.receive_json(); ws.receive_json()
        r = client.post("/api/v1/personalization/apply-to-devices", headers=auth)
        assert sorted(r.json()["updated"]) == sorted([d1, d2["public_id"]])
        pushed = ws.receive_json()
        assert pushed["type"] == "config" and (pushed["voice_gender"], pushed["character_id"], pushed["language"],
                                               pushed["wake_word"]) == ("female", "wise", "fr", "alexa")
    s = client.get(f"/api/v1/devices/{d1}/settings", headers=auth).json()
    assert s["character_id"] == "wise" and s["revision"] == 2
    # чужие устройства не затрагиваются
    other = hdr(register(client, email="other@example.com"))
    assert client.post("/api/v1/personalization/apply-to-devices", headers=other).json()["updated"] == []


# ---------------------------------------------------------------- шлюз: настройки устройства доходят до конвейера
def test_gateway_passes_current_settings_and_localises_phrases(client, auth, assistant):
    import base64
    d = client.post("/api/v1/devices", json={}, headers=auth).json()
    client.patch(f"/api/v1/devices/{d['public_id']}/settings", json={"language": "es", "voice_gender": "female",
                                                                     "character_id": "humorous"}, headers=auth)
    with client.websocket_connect("/noctis/v1/", headers={"X-Device-Id": d["public_id"], "X-Device-Token": d["device_token"]}) as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "start_recording"})
        ws.send_json({"type": "audio_chunk", "index": 0, "data": base64.b64encode(b"\x00" * 100).decode()})
        ws.send_json({"type": "stop_recording"})               # слишком коротко → фраза на испанском
        texts = []
        while True:
            m = ws.receive_json()
            if m["type"] == "text_response":
                texts.append(m["text"])
            if m["type"] == "audio_response_end":
                break
    assert texts == ["Demasiado corto, repítelo, por favor."]
    tts_calls = [c for c in assistant.calls if c[0] == "tts"]
    assert tts_calls and tts_calls[-1][1].startswith("Demasiado")


# ---------------------------------------------------------------- реальный адаптер NoctisAssistant (модели подменены)
class FakeModel:
    speakers = ["aidar", "baya", "xenia"]

    def __init__(self):
        self.calls = []

    def apply_tts(self, text, speaker, sample_rate, **kw):
        import numpy as np
        self.calls.append((text, speaker, sample_rate, kw))
        return np.ones(sample_rate // 10, dtype="float32") * 0.3


@pytest.fixture
def real_assistant(monkeypatch):
    pytest.importorskip("scipy")
    from noctis_server.voice import pipeline
    from noctis_server.voice.assistant import NoctisAssistant

    a = NoctisAssistant(preload=False)
    a.chat_calls = []

    def fake_chat(**kw):
        a.chat_calls.append(kw)
        yield {"message": {"content": "Hello there. This is the answer. "}, "done": False}
        yield {"message": {"content": "Bye."}, "done": True, "done_reason": "stop"}

    monkeypatch.setattr(pipeline, "chat", fake_chat)
    models = {lang: FakeModel() for lang in LANGUAGES}
    for lang, m in models.items():
        m.speakers = [*VOICES[lang].values(), "other"] if lang != "fr" else ["only_this"]  # у fr нет голосов из справочника
    monkeypatch.setattr(a, "load_tts", lambda language="ru": models[language])
    a.tts_models = models
    a.models = models
    return a


def test_assistant_applies_character_language_and_length(real_assistant):
    a = real_assistant
    from collections import deque
    hist = deque(maxlen=10)
    out = list(a.ask_llm_stream("explain how a fridge works", hist, {"language": "en", "character_id": "sarcastic"}))
    assert out and a.chat_calls
    call = a.chat_calls[0]
    system = call["messages"][0]["content"]
    assert "sarcastic" in system and "Always answer in English" in system and "six to ten sentences" in system
    assert call["options"]["num_predict"] == 600       # длинный режим по триггеру "explain"
    assert hist[-1]["role"] == "assistant"              # история ведётся

    list(a.ask_llm_stream("привет", None, {"language": "ru", "character_id": "formal"}))
    assert "официальный" in a.chat_calls[1]["messages"][0]["content"]


def test_assistant_llm_error_uses_local_language(real_assistant, monkeypatch):
    from noctis_server.voice import pipeline

    def boom(**kw):
        raise RuntimeError("ollama down")
        yield

    monkeypatch.setattr(pipeline, "chat", boom)
    out = list(real_assistant.ask_llm_stream("hallo", None, {"language": "de"}))
    assert out == ["Entschuldigung, beim Erzeugen der Antwort ist ein Fehler aufgetreten."]


def test_assistant_tts_picks_voice_and_language(real_assistant):
    a = real_assistant
    a.tts_pcm16("Hello world, this is a test.", {"language": "en", "voice_gender": "female"})
    assert a.models["en"].calls[0][1] == VOICES["en"]["female"]
    a.tts_pcm16("Bonjour tout le monde, ceci est un test.", {"language": "fr", "voice_gender": "male"})
    assert a.models["fr"].calls[0][1] == "only_this"                      # голоса нет в модели — запасной
    a.tts_pcm16("Привет, как дела.", {"voice_gender": "female"})
    text, speaker, rate, kw = a.models["ru"].calls[0]
    assert speaker == "xenia" and kw.get("put_accent") is True            # русский: женский голос + ударения
    a.tts_pcm16("Привет, как дела.", None)
    assert a.models["ru"].calls[1][1] == "aidar"                          # по умолчанию мужской


def test_assistant_numbers_spoken_in_language(real_assistant):
    a = real_assistant
    a.tts_pcm16("It is 12 o'clock", {"language": "en"})
    assert "twelve" in a.models["en"].calls[0][0]
    a.tts_pcm16("Es ist 7 Uhr", {"language": "de"})
    assert "sieben" in a.models["de"].calls[0][0]
