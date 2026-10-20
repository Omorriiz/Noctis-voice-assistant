"""Этап 4: контейнер NWWM, хранение и доставка моделей слова пробуждения, логика прошивки (настоящий Си-код)."""
import hashlib
import os
import sys
from pathlib import Path

import pytest

from noctis_server import nwwm

from .conftest import make_admin, register

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.virtual_speaker import (ENGINE_ESP_SR, ENGINE_MWW, Core, VirtualSpeaker,  # noqa: E402
                                   build_core_library)

MANIFEST = {"version": 1, "wake_word": "nils", "label": "Нильс", "feature_step_size": 10,
            "probability_cutoff": 0.97, "sliding_window_size": 5, "tensor_arena_size": 26080}


def fake_model(n=2000, seed=1):
    """Похоже на TFLite: flatbuffer-идентификатор TFL3 по смещению 4 (содержимое для проверок контейнера не важно)."""
    body = bytearray(os.urandom(n))
    body[:8] = b"\x1c\x00\x00\x00TFL3"
    return bytes(body)


def hdr(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture(scope="session")
def core(tmp_path_factory):
    try:
        return Core(build_core_library(tmp_path_factory.mktemp("lib4")))
    except Exception as e:
        pytest.skip(f"нет компилятора: {e}")


@pytest.fixture
def admin(client, auth):
    make_admin()
    return hdr(client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "correct-horse-1"}).json())


# ---------------------------------------------------------------- контейнер (Python)
def test_container_roundtrip_and_alignment():
    model = fake_model()
    blob = nwwm.pack(MANIFEST, model)
    manifest, parsed = nwwm.parse(blob)
    assert manifest["wake_word"] == "nils" and parsed == model
    mlen = int.from_bytes(blob[8:12], "little")
    model_off = (48 + mlen + 15) & ~15
    assert blob[model_off:] == model and model_off % 16 == 0       # модель выровнена для mmap


@pytest.mark.parametrize("mutate,expected", [
    (lambda b: b"XXXX" + b[4:], "bad magic"),
    (lambda b: b[:-1], "bad lengths"),
    (lambda b: b[:20], "bad (size|lengths)"),
    (lambda b: b[:-1] + bytes([b[-1] ^ 1]), "checksum"),
    (lambda b: b[:60] + bytes([b[60] ^ 1]) + b[61:], "checksum"),   # испорчен манифест
])
def test_container_rejects_corruption(mutate, expected):
    blob = nwwm.pack(MANIFEST, fake_model())
    with pytest.raises(nwwm.ContainerError, match=expected):
        nwwm.parse(mutate(blob))


def test_container_rejects_bad_manifest_and_model():
    with pytest.raises(nwwm.ContainerError, match="not a TFLite"):
        nwwm.parse(nwwm.pack(MANIFEST, b"\x00" * 100))
    with pytest.raises(nwwm.ContainerError, match="10 ms"):
        nwwm.parse(nwwm.pack({**MANIFEST, "feature_step_size": 20}, fake_model()))
    with pytest.raises(nwwm.ContainerError, match="lacks"):
        nwwm.parse(nwwm.pack({"wake_word": "x"}, fake_model()))
    with pytest.raises(nwwm.ContainerError, match="detection"):
        nwwm.parse(nwwm.pack({**MANIFEST, "probability_cutoff": 1.5}, fake_model()))
    with pytest.raises(nwwm.ContainerError, match="too large"):
        nwwm.pack(MANIFEST, fake_model(2 * 1024 * 1024))


# ---------------------------------------------------------------- паритет Python ↔ Си (настоящий код прошивки)
def test_sha256_matches_hashlib(core):
    for n in (0, 1, 55, 56, 57, 63, 64, 65, 119, 120, 1000, 100_000):
        data = os.urandom(n)
        assert core.sha256(data) == hashlib.sha256(data).digest(), n


def test_c_parser_accepts_python_container(core):
    blob = nwwm.pack({**MANIFEST, "label": "Ноктис Test"}, fake_model())
    parsed, err = core.parse_container(blob + b"\xff" * 500)   # после контейнера в разделе — мусор
    assert err is None and parsed["wake_word"] == "nils" and parsed["label"] == "Ноктис Test"
    assert parsed["window"] == 5 and parsed["arena"] == 26080 and abs(parsed["cutoff"] - 0.97) < 1e-6
    assert parsed["total"] == len(blob) == core.container_total(blob + b"\xff" * 500)


@pytest.mark.parametrize("mutate", [
    lambda b: b"XXXX" + b[4:], lambda b: b[:-1], lambda b: b[:-1] + bytes([b[-1] ^ 1]),
    lambda b: b[:60] + bytes([b[60] ^ 1]) + b[61:], lambda b: b"\xff" * len(b), lambda b: b[:30],
])
def test_c_and_python_reject_the_same_corruptions(core, mutate):
    blob = mutate(nwwm.pack(MANIFEST, fake_model()))
    avail = len(blob)
    _, c_err = core.parse_container(blob, avail)
    with pytest.raises(nwwm.ContainerError):
        nwwm.parse(blob)
    assert c_err is not None


def test_c_rejects_what_python_rejects_in_manifest(core):
    for bad in ({**MANIFEST, "feature_step_size": 20}, {**MANIFEST, "probability_cutoff": 1.5},
                {**MANIFEST, "sliding_window_size": 0}, {**MANIFEST, "sliding_window_size": 21}):
        blob = nwwm.pack(bad, fake_model())   # pack() значения не проверяет — это делает parse() / прошивка
        with pytest.raises(nwwm.ContainerError):
            nwwm.parse(blob)
        assert core.parse_container(blob)[0] is None


def test_engine_selection(core):
    assert core.engine("nils") == (ENGINE_MWW, None)
    assert core.engine("custom:Привет Ноктис")[0] == ENGINE_MWW
    assert core.engine("privet_esp") == (ENGINE_ESP_SR, "privetesp")
    assert core.engine("computer") == (ENGINE_ESP_SR, "computer")


# ---------------------------------------------------------------- сервер
def upload(client, adm, blob, word="nils"):
    return client.put(f"/api/v1/wakewords?wake_word={word}", content=blob, headers=adm)


def test_upload_requires_admin_and_valid_container(client, auth, admin):
    blob = nwwm.pack(MANIFEST, fake_model())
    plain = hdr(register(client, email="plain@example.com"))   # обычный пользователь (auth к этому моменту уже администратор)
    assert client.put("/api/v1/wakewords?wake_word=nils", content=blob, headers=plain).status_code == 403
    assert client.put("/api/v1/wakewords?wake_word=nils", content=blob).status_code == 401
    assert upload(client, admin, b"garbage").status_code == 422
    assert upload(client, admin, blob, "siri").status_code == 422          # модель бывает только у «nils»
    assert upload(client, admin, blob, "alexa").status_code == 422         # у «Alexa» своя модель в esp-sr
    r = upload(client, admin, blob)
    assert r.status_code == 201 and r.json()["sha256"] == hashlib.sha256(blob).hexdigest() and r.json()["label"] == "Нильс"
    assert [m["wake_word"] for m in client.get("/api/v1/wakewords", headers=auth).json()] == ["nils"]
    assert upload(client, admin, nwwm.pack({**MANIFEST, "label": "v2"}, fake_model(3000)), "custom:Привет Ноктис").status_code == 422
    assert upload(client, admin, nwwm.pack({**MANIFEST, "label": "v2"}, fake_model(3000))).status_code == 201     # замена модели «nils»
    assert client.delete("/api/v1/wakewords?wake_word=nils", headers=admin).status_code == 204
    assert client.delete("/api/v1/wakewords?wake_word=nils", headers=admin).status_code == 404


def test_catalog_reports_model_readiness(client, auth, admin):
    def ready():
        return {w["id"]: w["model_ready"] for w in client.get("/api/v1/personalization/catalog").json()["wake_words"]}
    r = ready()
    assert set(r) == {"nils", "alexa"} and r["nils"] is False and r["alexa"] is True
    upload(client, admin, nwwm.pack(MANIFEST, fake_model()))
    assert ready()["nils"] is True


def test_config_carries_wake_model_for_matching_word_only(client, auth, admin):
    blob = nwwm.pack(MANIFEST, fake_model())
    upload(client, admin, blob)
    pid = client.post("/api/v1/devices", json={}, headers=auth).json()["public_id"]
    sha = hashlib.sha256(blob).hexdigest()
    with_model = client.patch(f"/api/v1/devices/{pid}/settings", json={"wake_word": "nils"}, headers=auth)
    assert with_model.status_code == 200
    d = client.post("/api/v1/devices", json={}, headers=auth).json()
    with client.websocket_connect("/noctis/v1/", headers={"X-Device-Id": d["public_id"], "X-Device-Token": d["device_token"]}) as ws:
        ws.receive_json()
        cfg = ws.receive_json()
        assert cfg["wake_model"] == {"sha256": sha, "size": len(blob)}
        client.patch(f"/api/v1/devices/{d['public_id']}/settings", json={"wake_word": "alexa"}, headers=auth)
        assert ws.receive_json()["wake_model"] is None


def test_device_downloads_model_by_hash_with_device_auth(client, auth, admin):
    blob = nwwm.pack(MANIFEST, fake_model())
    sha = upload(client, admin, blob).json()["sha256"]
    d = client.post("/api/v1/devices", json={}, headers=auth).json()
    dev = {"X-Device-Id": d["public_id"], "X-Device-Token": d["device_token"]}
    assert client.get(f"/api/v1/device/wakeword/{sha}").status_code == 401
    assert client.get(f"/api/v1/device/wakeword/{sha}", headers=auth).status_code == 401          # токен пользователя не годится
    assert client.get(f"/api/v1/device/wakeword/{sha}", headers={**dev, "X-Device-Token": "x"}).status_code == 401
    assert client.get(f"/api/v1/device/wakeword/{sha}", headers=dev).content == blob
    assert client.get("/api/v1/device/wakeword/" + "0" * 64, headers=dev).status_code == 404
    assert client.get("/api/v1/device/wakeword/../../etc/passwd", headers=dev).status_code in (404, 422)


def test_new_model_is_pushed_to_online_device(client, auth, admin):
    d = client.post("/api/v1/devices", json={}, headers=auth).json()
    with client.websocket_connect("/noctis/v1/", headers={"X-Device-Id": d["public_id"], "X-Device-Token": d["device_token"]}) as ws:
        ws.receive_json()
        assert ws.receive_json()["wake_model"] is None
        blob = nwwm.pack(MANIFEST, fake_model())
        upload(client, admin, blob)
        pushed = ws.receive_json()
        assert pushed["type"] == "config" and pushed["wake_model"]["sha256"] == hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------- виртуальная колонка: синхронизация модели (логика control.c / wakeword_sync.c)
@pytest.fixture
def paired(client, auth, core, tmp_path):
    d = client.post("/api/v1/devices", json={}, headers=auth).json()
    sp = VirtualSpeaker(core, tmp_path / "nvs.json")
    assert sp.pair({"server": "testserver", "tls": False, "device_id": d["public_id"], "token": d["device_token"]})
    return d["public_id"], sp


def http_get(client):
    return lambda url, headers: client.get(url.split("://testserver", 1)[1], headers=headers)


def connect_and_config(client, sp):
    ws_cm = client.websocket_connect("/noctis/v1/", headers=sp.headers())
    ws = ws_cm.__enter__()
    sp.handle(ws, ws.receive_json(), http_get(client))   # hello
    sp.handle(ws, ws.receive_json(), http_get(client))   # config
    return ws_cm, ws


def test_without_model_falls_back_to_privet_esp_then_installs(client, auth, admin, paired):
    pid, sp = paired
    assert sp.engine == ENGINE_MWW and sp.engine_running == ENGINE_ESP_SR and sp.wn_model == "privetesp"   # модели нет
    blob = nwwm.pack(MANIFEST, fake_model())
    upload(client, admin, blob)
    cm, ws = connect_and_config(client, sp)
    try:
        assert sp.wake_log == [(hashlib.sha256(blob).hexdigest(), "ok")]
        assert sp.restarts == 1 and sp.engine_running == ENGINE_MWW           # после перезапуска слушает «Нильс»
        assert sp.installed_sha == hashlib.sha256(blob).hexdigest()
    finally:
        cm.__exit__(None, None, None)
    # повторное подключение с той же моделью — скачивания нет
    cm, ws = connect_and_config(client, sp)
    try:
        assert len(sp.wake_log) == 1 and sp.restarts == 1
    finally:
        cm.__exit__(None, None, None)


def test_corrupted_download_is_never_written(client, auth, admin, paired):
    pid, sp = paired
    blob = nwwm.pack(MANIFEST, fake_model())
    sha = upload(client, admin, blob).json()["sha256"]
    from noctis_server.routers.wakewords import WAKE_DIR
    (WAKE_DIR / f"{sha}.nwwm").write_bytes(blob[:-1] + bytes([blob[-1] ^ 1]))   # файл на сервере испорчен
    before = sp.wake_partition
    cm, ws = connect_and_config(client, sp)
    try:
        assert sp.wake_log[-1][1] == "sha256 mismatch"
        assert sp.wake_partition == before and sp.restarts == 0 and sp.engine_running == ENGINE_ESP_SR
    finally:
        cm.__exit__(None, None, None)


def test_valid_hash_but_broken_container_is_rejected(client, auth, admin, paired, monkeypatch):
    """Сервер раздаёт мусор с «правильным» хэшем: прошивка всё равно проверяет структуру до записи."""
    pid, sp = paired
    blob = nwwm.pack(MANIFEST, fake_model())
    upload(client, admin, blob)
    garbage = b"G" * len(blob)
    from noctis_server.models import WakeModel
    from noctis_server.db import SessionLocal
    from noctis_server.routers.wakewords import WAKE_DIR
    gsha = hashlib.sha256(garbage).hexdigest()
    (WAKE_DIR / f"{gsha}.nwwm").write_bytes(garbage)
    with SessionLocal() as db:
        row = db.query(WakeModel).one()
        row.sha256, row.filename = gsha, f"{gsha}.nwwm"
        db.commit()
    cm, ws = connect_and_config(client, sp)
    try:
        assert sp.wake_log[-1][1].startswith("bad container") and sp.restarts == 0
    finally:
        cm.__exit__(None, None, None)


def test_changing_wake_word_restarts_into_new_engine(client, auth, admin, paired):
    pid, sp = paired
    upload(client, admin, nwwm.pack(MANIFEST, fake_model()))
    cm, ws = connect_and_config(client, sp)
    try:
        assert sp.engine_running == ENGINE_MWW and sp.restarts == 1
        client.patch(f"/api/v1/devices/{pid}/settings", json={"wake_word": "alexa"}, headers=auth)
        sp.handle(ws, ws.receive_json(), http_get(client))
        assert sp.engine_running == ENGINE_ESP_SR and sp.wn_model == "alexa" and sp.restarts == 2
        client.patch(f"/api/v1/devices/{pid}/settings", json={"voice_gender": "female"}, headers=auth)   # не слово — без перезапуска
        sp.handle(ws, ws.receive_json(), http_get(client))
        assert sp.restarts == 2
        client.patch(f"/api/v1/devices/{pid}/settings", json={"wake_word": "nils"}, headers=auth)
        sp.handle(ws, ws.receive_json(), http_get(client))
        assert sp.engine_running == ENGINE_MWW and sp.restarts == 3        # модель уже стоит — скачивание не нужно
        assert len(sp.wake_log) == 1
    finally:
        cm.__exit__(None, None, None)


def test_custom_phrases_are_not_available_to_users(client, auth, admin, paired):
    pid, sp = paired
    word = "custom:Привет Ноктис"
    blob = nwwm.pack({**MANIFEST, "wake_word": word, "label": "Привет Ноктис"}, fake_model(3000))
    assert upload(client, admin, blob, word).status_code == 422
    assert client.patch(f"/api/v1/devices/{pid}/settings", json={"wake_word": word}, headers=auth).status_code == 422


def test_removed_word_in_old_data_is_sent_to_the_board_as_nils(client, auth, admin, paired):
    """Старая запись в базе («jarvis») не уходит на плату: config несёт «nils» и его модель."""
    from noctis_server.db import SessionLocal
    from noctis_server.models import DeviceSettings
    pid, sp = paired
    upload(client, admin, nwwm.pack(MANIFEST, fake_model()))
    with SessionLocal() as db:
        db.query(DeviceSettings).update({"wake_word": "jarvis"})
        db.commit()
    cm, ws = connect_and_config(client, sp)
    try:
        assert sp.settings.wake_word.decode() == "nils" and sp.engine_running == ENGINE_MWW
    finally:
        cm.__exit__(None, None, None)


def test_seed_imports_bundled_models_once(tmp_path, monkeypatch):
    from noctis_server import services
    from noctis_server.db import SessionLocal
    from noctis_server.models import WakeModel
    from noctis_server.config import BASE_DIR

    src = tmp_path / "models"
    src.mkdir()
    (src / "nils.nwwm").write_bytes(nwwm.pack(MANIFEST, fake_model()))
    (src / "broken.nwwm").write_bytes(b"not a model")
    # копии файлов — во временную папку, а не в data/wakewords
    monkeypatch.setattr("noctis_server.config.BASE_DIR", tmp_path / "srv")
    with SessionLocal() as db:
        assert services.seed_wake_models(db, src) == ["nils"]          # битый файл пропущен
        assert services.seed_wake_models(db, src) == []                # повторно — ничего
        row = db.query(WakeModel).one()
        assert row.wake_word == "nils" and (tmp_path / "srv" / "data" / "wakewords" / row.filename).exists()
        row.notes = "admin"                                            # загруженное админом не затирается
        db.commit()
        assert services.seed_wake_models(db, src) == []


BUNDLED = sorted((Path(__file__).resolve().parents[2] / "wakeword" / "models").glob("*.nwwm"))


@pytest.mark.skipif(not BUNDLED, reason="готовых моделей нет в wakeword/models")
@pytest.mark.parametrize("path", BUNDLED, ids=lambda p: p.name)
def test_bundled_model_is_valid_for_server_and_firmware(core, path):
    """Готовая модель из проекта проходит проверку и сервера (Python), и прошивки (настоящий Си-код)."""
    blob = path.read_bytes()
    manifest, model = nwwm.parse(blob)
    parsed, err = core.parse_container(blob)
    assert err is None, err
    assert parsed["wake_word"] == manifest["wake_word"] and parsed["window"] == manifest["sliding_window_size"]
    assert parsed["model_len"] == len(model) and parsed["total"] == len(blob)
    assert manifest["feature_step_size"] == 10 and manifest["wake_word"] in ("nils",) or manifest["wake_word"].startswith("custom:")
    assert len(blob) < 1024 * 1024 - 4096              # помещается в раздел wakeword вместе с запасом
    assert parsed["arena"] >= 8192


@pytest.mark.skipif(not BUNDLED, reason="готовых моделей нет в wakeword/models")
def test_server_start_imports_bundled_nils_model(tmp_path, monkeypatch, assistant):
    """При старте сервер сам подключает готовую модель «Нильс»: каталог показывает model_ready, config несёт wake_model."""
    from fastapi.testclient import TestClient
    from noctis_server.config import settings
    from noctis_server.main import create_app

    monkeypatch.setattr("noctis_server.config.BASE_DIR", tmp_path / "srv")
    monkeypatch.setattr(settings, "seed_wake_models", True)
    monkeypatch.setattr(settings, "wake_models_dir", str(BUNDLED[0].parent))
    monkeypatch.setattr("noctis_server.routers.wakewords.WAKE_DIR", tmp_path / "srv" / "data" / "wakewords")
    with TestClient(create_app(assistant)) as c:
        words = {w["id"]: w["model_ready"] for w in c.get("/api/v1/personalization/catalog").json()["wake_words"]}
        assert words["nils"] is True
        auth = {"Authorization": "Bearer " + register(c)["access_token"]}
        d = c.post("/api/v1/devices", json={}, headers=auth).json()      # слово по умолчанию — nils
        with c.websocket_connect("/noctis/v1/", headers={"X-Device-Id": d["public_id"], "X-Device-Token": d["device_token"]}) as ws:
            ws.receive_json()
            wm = ws.receive_json()["wake_model"]
            assert wm and wm["size"] == BUNDLED[0].stat().st_size
            dev = {"X-Device-Id": d["public_id"], "X-Device-Token": d["device_token"]}
            assert c.get(f"/api/v1/device/wakeword/{wm['sha256']}", headers=dev).content == BUNDLED[0].read_bytes()
