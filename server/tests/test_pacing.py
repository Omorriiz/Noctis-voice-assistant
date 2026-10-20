"""Pacer: голос опережает воспроизведение не больше чем на TTS_LEAD_SEC, а «догнавшая» плата не вызывает лишних пауз."""
import asyncio
import time

from noctis_server.voice import gateway
from noctis_server.voice.gateway import BYTES_PER_SEC, Pacer


def timed(coro):
    t0 = time.monotonic()
    asyncio.run(coro)
    return time.monotonic() - t0


def test_no_wait_within_lead(monkeypatch):
    monkeypatch.setattr(gateway, "TTS_LEAD_SEC", 1.0)

    async def go():
        p = Pacer()
        for _ in range(8):                                   # 8 × 0,1 с = 0,8 с < опережения 1 с
            await p.wait(BYTES_PER_SEC // 10)

    assert timed(go()) < 0.25


def test_waits_when_ahead_of_playback(monkeypatch):
    monkeypatch.setattr(gateway, "TTS_LEAD_SEC", 0.2)

    async def go():
        p = Pacer()
        for _ in range(8):                                   # 0,8 с звука при опережении 0,2 с: ждать ≈ 0,6 с
            await p.wait(BYTES_PER_SEC // 10)

    elapsed = timed(go())
    assert 0.45 < elapsed < 1.2, elapsed


def test_caught_up_device_gets_fresh_lead(monkeypatch):
    """Предложение синтезировалось дольше, чем звучало: плата доиграла — опережение считается заново, без «долга» пауз."""
    monkeypatch.setattr(gateway, "TTS_LEAD_SEC", 0.3)

    async def go():
        p = Pacer()
        await p.wait(BYTES_PER_SEC // 10)                     # 0,1 с звука
        await asyncio.sleep(0.4)                              # синтез следующего: плата уже доиграла
        t = time.monotonic()
        await p.wait(BYTES_PER_SEC // 10)
        assert time.monotonic() - t < 0.15

    asyncio.run(go())
