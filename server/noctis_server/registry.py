"""Реестр подключённых по WebSocket устройств (public_id → соединение) и их текущего состояния."""
online_devices: dict[str, object] = {}

# Последний статус OTA по устройству: {public_id: {"version","state","error"}}
ota_status: dict[str, dict] = {}

# Устройства, у которых сейчас звонит будильник/таймер: {public_id: момент_окончания (unix)}
ringing: dict[str, float] = {}

# Смена сети Wi-Fi: {public_id: {"request_id", "ssid", "state": pending|ok|failed, "error", "at"}}. Пароль не хранится.
wifi_changes: dict[str, dict] = {}

# Музыкальные сессии: {public_id: MusicSession} (см. media/player.py)
players: dict[str, object] = {}
