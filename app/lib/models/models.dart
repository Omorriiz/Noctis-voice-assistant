// Модели ответов API сервера Noctis (поля совпадают с server/noctis_server/schemas.py).

class DeviceSettings {
  DeviceSettings({
    required this.wakeWord,
    required this.voiceGender,
    required this.characterId,
    required this.language,
    required this.volume,
    required this.timezone,
    this.homeCity = '',
  });

  final String wakeWord;
  final String voiceGender;
  final String characterId;
  final String language;
  final int volume;
  final String timezone;
  final String homeCity;

  factory DeviceSettings.fromJson(Map<String, dynamic> j) => DeviceSettings(
        wakeWord: j['wake_word'] as String,
        voiceGender: j['voice_gender'] as String,
        characterId: j['character_id'] as String,
        language: j['language'] as String,
        volume: (j['volume'] as num).toInt(),
        timezone: j['timezone'] as String,
        homeCity: (j['home_city'] as String?) ?? '',
      );
}

class Device {
  Device({
    required this.publicId,
    required this.name,
    required this.firmwareVersion,
    required this.lastSeen,
    required this.online,
    required this.settings,
  });

  final String publicId;
  final String name;
  final String firmwareVersion;
  final DateTime? lastSeen;
  final bool online;
  final DeviceSettings settings;

  factory Device.fromJson(Map<String, dynamic> j) => Device(
        publicId: j['public_id'] as String,
        name: j['name'] as String,
        firmwareVersion: (j['firmware_version'] ?? '') as String,
        lastSeen: j['last_seen'] == null ? null : DateTime.tryParse('${j['last_seen']}Z')?.toLocal() ?? DateTime.tryParse(j['last_seen'] as String),
        online: (j['online'] ?? false) as bool,
        settings: DeviceSettings.fromJson(j['settings'] as Map<String, dynamic>),
      );
}

/// Ответ создания устройства: токен показывается один раз и передаётся колонке по Bluetooth.
class CreatedDevice {
  CreatedDevice(this.device, this.token);
  final Device device;
  final String token;

  factory CreatedDevice.fromJson(Map<String, dynamic> j) => CreatedDevice(Device.fromJson(j), j['device_token'] as String);
}

class UserInfo {
  UserInfo({required this.email, required this.displayName, required this.role});
  final String email;
  final String displayName;
  final String role;

  String get title => displayName.isNotEmpty ? displayName : email;

  factory UserInfo.fromJson(Map<String, dynamic> j) =>
      UserInfo(email: j['email'] as String, displayName: (j['display_name'] ?? '') as String, role: (j['role'] ?? 'user') as String);
}

class TimerItem {
  TimerItem({required this.id, required this.label, required this.durationSec, required this.remainingSec, required this.state});
  final int id;
  final String label;
  final int durationSec;
  final int remainingSec;
  final String state;

  bool get paused => state == 'paused';

  factory TimerItem.fromJson(Map<String, dynamic> j) => TimerItem(
        id: (j['id'] as num).toInt(),
        label: (j['label'] ?? '') as String,
        durationSec: (j['duration_sec'] as num).toInt(),
        remainingSec: (j['remaining_sec'] as num).toInt(),
        state: (j['state'] ?? 'running') as String,
      );
}

class AlarmItem {
  AlarmItem({required this.id, required this.hour, required this.minute, required this.weekdays, required this.date, required this.label, required this.melody, required this.enabled});
  final int id;
  final int hour;
  final int minute;
  final List<int> weekdays;
  final String? date;
  final String label;
  final String melody;
  final bool enabled;

  String get time => '${hour.toString().padLeft(2, '0')}:${minute.toString().padLeft(2, '0')}';

  factory AlarmItem.fromJson(Map<String, dynamic> j) => AlarmItem(
        id: (j['id'] as num).toInt(),
        hour: (j['hour'] as num).toInt(),
        minute: (j['minute'] as num).toInt(),
        weekdays: ((j['weekdays'] ?? const []) as List).map((e) => (e as num).toInt()).toList(),
        date: j['date'] as String?,
        label: (j['label'] ?? '') as String,
        melody: (j['melody'] ?? 'default') as String,
        enabled: (j['enabled'] ?? true) as bool,
      );
}

class Track {
  Track({required this.id, required this.title, required this.artist, required this.source, required this.license});
  final int id;
  final String title;
  final String artist;
  final String source;
  final String license;

  factory Track.fromJson(Map<String, dynamic> j) => Track(
        id: (j['id'] as num).toInt(),
        title: (j['title'] ?? '') as String,
        artist: (j['artist'] ?? '') as String,
        source: (j['source'] ?? '') as String,
        license: (j['license'] ?? '') as String,
      );
}

class PlayerInfo {
  PlayerInfo({required this.state, required this.index, required this.total, this.title, this.artist});
  final String state;
  final int index;
  final int total;
  final String? title;
  final String? artist;

  bool get active => title != null && state != 'stopped';
  bool get playing => state == 'playing';

  factory PlayerInfo.fromJson(Map<String, dynamic> j) {
    final t = j['track'] as Map<String, dynamic>?;
    return PlayerInfo(
        state: (j['state'] ?? 'stopped') as String,
        index: ((j['index'] ?? 0) as num).toInt(),
        total: ((j['total'] ?? 0) as num).toInt(),
        title: t?['title'] as String?,
        artist: t?['artist'] as String?);
  }
}

class HistoryItem {
  HistoryItem({required this.createdAt, required this.userText, required this.assistantText});
  final DateTime? createdAt;
  final String userText;
  final String assistantText;

  factory HistoryItem.fromJson(Map<String, dynamic> j) => HistoryItem(
        createdAt: DateTime.tryParse('${j['created_at']}Z')?.toLocal() ?? DateTime.tryParse('${j['created_at']}'),
        userText: (j['user_text'] ?? '') as String,
        assistantText: (j['assistant_text'] ?? '') as String,
      );
}

class CatalogOption {
  CatalogOption(this.id, this.label, {this.description = '', this.ready = true});
  final String id;
  final String label;
  final String description;
  final bool ready;
}

class Catalog {
  Catalog({required this.languages, required this.characters, required this.wakeWords});
  final List<CatalogOption> languages;
  final List<CatalogOption> characters;
  final List<CatalogOption> wakeWords;

  factory Catalog.fromJson(Map<String, dynamic> j) => Catalog(
        languages: [for (final l in j['languages'] as List) CatalogOption(l['id'] as String, l['name'] as String)],
        characters: [for (final c in j['characters'] as List) CatalogOption(c['id'] as String, c['name'] as String, description: (c['description'] ?? '') as String)],
        wakeWords: [for (final w in j['wake_words'] as List) CatalogOption(w['id'] as String, w['label'] as String, ready: (w['model_ready'] ?? true) as bool)],
      );
}

/// Итог смены Wi‑Fi: none | pending | ok | failed | timeout.
class WifiStatus {
  WifiStatus({required this.state, this.ssid = '', this.error = ''});
  final String state;
  final String ssid;
  final String error;

  bool get pending => state == 'pending';

  factory WifiStatus.fromJson(Map<String, dynamic> j) => WifiStatus(state: j['state'] as String, ssid: (j['ssid'] ?? '') as String, error: (j['error'] ?? '') as String);
}
