"""Jamendo на форме РЕАЛЬНЫХ ответов API (записаны 19.10.2026): повтор пустых ответов, жанры, лицензии, отказ ключа."""
import threading

import httpx
import pytest

from noctis_server.config import settings
from noctis_server.media import jamendo

REAL_ITEM = {
    "id": "2292678", "name": "Jazz", "duration": 133, "artist_id": "603507", "artist_name": "Where Memories Rest",
    "artist_idstr": "Where_Memories_Rest", "album_name": "Jazz", "album_id": "621123",
    "license_ccurl": "http://creativecommons.org/licenses/by-nc-nd/3.0/", "position": 1, "releasedate": "2025-12-18",
    "audio": "https://prod-1.storage.jamendo.com/?trackid=2292678&format=mp32&from=abc%3D%3D%7Cdef%3D%3D",
    "audiodownload": "https://prod-1.storage.jamendo.com/download/track/2292678/mp32/",
}
OK_BODY = {"headers": {"status": "success", "code": 0, "error_message": "", "warnings": "", "results_count": 1}, "results": [REAL_ITEM]}
EMPTY_BODY = {"headers": {"status": "success", "code": 0, "error_message": "", "warnings": "", "results_count": 0}, "results": []}


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setattr(settings, "jamendo_client_id", "test-client")


def use(monkeypatch, handler):
    monkeypatch.setattr(jamendo, "transport", httpx.MockTransport(handler))


def test_real_shape_parsed(monkeypatch):
    use(monkeypatch, lambda r: httpx.Response(200, json=OK_BODY))
    t = jamendo.search("jazz")[0]
    assert t["id"] == "2292678" and t["title"] == "Jazz" and t["artist"] == "Where Memories Rest"
    assert t["uri"].startswith("https://prod-1.storage.jamendo.com/") and t["duration"] == 133.0
    assert t["license"] == "CC BY-NC-ND 3.0"


def test_empty_success_replies_are_retried(monkeypatch):
    """Jamendo в 30–60 % случаев отвечает success с пустым списком — это не «ничего не найдено»."""
    calls = []
    lock = threading.Lock()

    def handler(request):
        with lock:
            calls.append(1)
            n = len(calls)
        return httpx.Response(200, json=OK_BODY if n >= 5 else EMPTY_BODY)    # первая пачка из 3 и два из второй — пустые

    use(monkeypatch, handler)
    assert jamendo.search("рок")[0]["id"] == "2292678"
    assert 5 <= len(calls) <= 6


def test_really_empty_search_returns_nothing_after_all_rounds(monkeypatch):
    calls = []
    use(monkeypatch, lambda r: calls.append(1) or httpx.Response(200, json=EMPTY_BODY))
    assert jamendo.search("qwertyuiop") == []
    assert len(calls) == jamendo.PARALLEL * jamendo.ROUNDS


def test_rejected_key_is_reported_separately(monkeypatch):
    body = {"headers": {"status": "failed", "code": 5, "error_message": "Jamendo Api Invalid Client Id Error: Your credential is not authorized.",
                        "results_count": 0}, "results": []}
    use(monkeypatch, lambda r: httpx.Response(200, json=body))
    with pytest.raises(jamendo.JamendoKeyError):
        jamendo.search("jazz")


def test_network_errors_use_last_good_answer_then_fail(monkeypatch):
    use(monkeypatch, lambda r: httpx.Response(200, json=OK_BODY))
    assert jamendo.search("jazz")
    jamendo._cache[("jazz", 10)] = (0.0, jamendo._cache[("jazz", 10)][1])       # кэш устарел, но сеть теперь недоступна
    use(monkeypatch, lambda r: httpx.Response(500))
    assert jamendo.search("jazz")[0]["id"] == "2292678"                        # старый удачный ответ лучше тишины
    with pytest.raises(jamendo.JamendoError):
        jamendo.search("something-new")


def test_fresh_cache_avoids_requests(monkeypatch):
    calls = []
    use(monkeypatch, lambda r: calls.append(1) or httpx.Response(200, json=OK_BODY))
    jamendo.search("jazz")
    n = len(calls)
    jamendo.search("Jazz ")
    assert len(calls) == n


@pytest.mark.parametrize("query,tag", [("джаз", "jazz"), ("Включи рок", "rock"), ("спокойный джаз", "jazz"),
                                       ("электронная музыка", "electronic"), ("классика", "classical"), ("рэп", "hiphop"),
                                       ("jazz", "jazz"), ("chill", "chillout"), ("хип-хоп", "hiphop"),
                                       ("популярная музыка", None), ("Daft Punk", None), ("Кино", None), ("", None), ("включи что-нибудь", None),
                                       ("Punk Goes Pop", None), ("рок музыка", "rock")])
def test_genre_mapping(query, tag):
    assert jamendo.genre_tag(query) == tag


def test_genre_goes_to_tags_and_text_to_search(monkeypatch):
    seen = []
    use(monkeypatch, lambda r: seen.append(dict(r.url.params)) or httpx.Response(200, json=OK_BODY))
    jamendo.search("джаз")
    assert seen[-1]["tags"] == "jazz" and "search" not in seen[-1] and seen[-1]["order"] == "popularity_week"
    jamendo._cache.clear()
    jamendo.search("Where Memories Rest")
    assert seen[-1]["search"] == "Where Memories Rest" and "tags" not in seen[-1] and seen[-1]["order"] == "relevance"
    jamendo._cache.clear()
    jamendo.search("")
    assert "tags" not in seen[-1] and "search" not in seen[-1] and seen[-1]["order"] == "popularity_week"
    assert seen[-1]["audioformat"] == "mp32" and seen[-1]["client_id"] == "test-client"


@pytest.mark.parametrize("url,label", [("http://creativecommons.org/licenses/by-nc-nd/3.0/", "CC BY-NC-ND 3.0"),
                                       ("https://creativecommons.org/licenses/by-sa/4.0/", "CC BY-SA 4.0"),
                                       ("https://creativecommons.org/publicdomain/zero/1.0/", "CC0"), ("", ""),
                                       ("https://example.com/license", "https://example.com/license")])
def test_license_label(url, label):
    assert jamendo.license_label(url) == label
