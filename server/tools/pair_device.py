"""Сопряжение колонки с ПК по BLE без мобильного приложения (до этапа 6).

Делает то же, что приложение: BLE-сессия (security 1, код = PoP с экрана платы), сначала endpoint
`noctis-config` (адрес сервера, device_id, токен), затем Wi-Fi. Готовый `esp_prov.py` не подходит:
он умеет слать данные только в endpoint `custom-data`.

Нужно: pip install bleak protobuf cryptography (в server/.venv) и пакет framework-espidf PlatformIO.

    python tools/pair_device.py --name NOCTIS_A1B2C3 --pop 12345678 --server 192.168.1.5:8000 \
        --device-id <public_id> --token <device_token> --ssid MyWiFi --password secret
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def find_esp_prov() -> Path:
    env = os.environ.get("ESP_PROV_DIR")
    cands = [Path(env)] if env else []
    cands.append(Path.home() / ".platformio" / "packages" / "framework-espidf" / "tools" / "esp_prov")
    for c in cands:
        if (c / "esp_prov.py").exists():
            return c
    sys.exit("esp_prov не найден: задайте ESP_PROV_DIR (папка tools/esp_prov из framework-espidf)")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="имя BLE с экрана платы, NOCTIS_XXXXXX")
    ap.add_argument("--pop", required=True, help="8-значный код сопряжения с экрана")
    ap.add_argument("--server", required=True, help="адрес ПК в локальной сети, например 192.168.1.5:8000")
    ap.add_argument("--device-id", required=True, help="public_id из POST /api/v1/devices")
    ap.add_argument("--token", required=True, help="device_token из POST /api/v1/devices")
    ap.add_argument("--ssid", required=True)
    ap.add_argument("--password", default="")
    ap.add_argument("--tls", action="store_true", help="сервер за https/wss")
    args = ap.parse_args()

    # плата принимает только host:port (буквы, цифры, . - : _): убираем http(s):// и хвостовой «/»
    server = args.server.strip()
    for scheme, tls in (("https://", True), ("http://", False), ("wss://", True), ("ws://", False)):
        if server.lower().startswith(scheme):
            server, args.tls = server[len(scheme):], args.tls or tls
            break
    server = server.split("/")[0]
    if not server or any(not (c.isalnum() or c in ".-:_") for c in server):
        print(f"Неверный адрес сервера: «{args.server}». Нужен вид 192.168.0.136:8000")
        return 1
    args.server = server
    print(f"Адрес сервера для платы: {server}" + (" (TLS)" if args.tls else ""))

    prov_dir = find_esp_prov()
    os.environ.setdefault("IDF_PATH", str(prov_dir.parent.parent))   # .../framework-espidf
    sys.path.insert(0, str(prov_dir))
    import esp_prov as ep          # noqa: E402
    import prov                    # noqa: E402

    tp = await ep.get_transport("ble", args.name)
    if tp is None:
        print("Не удалось подключиться по BLE. Плата в режиме сопряжения? Имя верное?")
        return 1
    try:
        sec = ep.get_security(1, 0, "", "", args.pop)
        if not await ep.establish_session(tp, sec):
            print("Сессия не установлена: проверьте код сопряжения (PoP)")
            return 1
        print("Сессия установлена")

        link = json.dumps({"server": args.server, "tls": args.tls,
                           "device_id": args.device_id, "token": args.token})
        reply = await tp.send_data("noctis-config", prov.custom_data_request(sec, link))
        answer = sec.decrypt_data(reply.encode("latin-1") if isinstance(reply, str) else reply)
        print("noctis-config:", answer.decode("utf-8", "replace"))
        if b'"ok"' not in answer:
            print("Плата отклонила данные сервера")
            return 1

        if not await ep.send_wifi_config(tp, sec, args.ssid, args.password):
            print("Не удалось передать Wi-Fi")
            return 1
        if not await ep.apply_wifi_config(tp, sec):
            print("Не удалось применить Wi-Fi")
            return 1
        print("Wi-Fi передан, плата подключается…")
        try:
            ok = await ep.wait_wifi_connected(tp, sec)
        except OSError:
            # Плата закрывает BLE, как только подключилась к Wi-Fi и серверу, — это успех, а не ошибка
            print("\nСоединение по Bluetooth закрыто платой — обычно это значит, что она уже подключилась.")
            print("Проверьте статус «онлайн» на странице /console и журнал монитора платы.")
            return 0
        return 0 if ok else 1
    finally:
        try:
            await tp.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
