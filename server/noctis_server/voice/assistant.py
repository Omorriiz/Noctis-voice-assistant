"""Голосовой конвейер с персонализацией устройства: язык (STT/LLM/TTS), голос и характер.
Выбор значений — в personalization.py (без GPU); здесь они передаются в модели."""
from .. import personalization as pz
from .pipeline import VoiceAssistant


class NoctisAssistant(VoiceAssistant):
    def transcribe(self, audio, device_settings: dict | None = None) -> str:
        return super().transcribe(audio, language=pz.normalize(device_settings)["language"])

    def ask_llm_stream(self, user_text: str, history=None, device_settings: dict | None = None):
        prompt, num_predict, mode = pz.build_system_prompt(device_settings, user_text)
        hint = pz.creative_hint(user_text, pz.normalize(device_settings)["language"])
        return super().ask_llm_stream(user_text, history=history, system_prompt=prompt, num_predict=num_predict,
                                      fallbacks=pz.fallbacks(device_settings), llm_user_text=(user_text + hint) if hint else None)

    def tts_pcm16(self, text: str, device_settings: dict | None = None) -> bytes:
        language, speaker = pz.voice_for(device_settings)
        return super().tts_pcm16(text, speaker=speaker, language=language)
