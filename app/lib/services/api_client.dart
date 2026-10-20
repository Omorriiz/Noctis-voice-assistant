import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../models/models.dart';
import 'token_store.dart';

/// Ошибка обращения к серверу. [status] == 0 — нет связи.
class ApiException implements Exception {
  ApiException(this.status, [this.detail = '']);
  final int status;
  final String detail;

  bool get isNetwork => status == 0;
  bool get isUnauthorized => status == 401;

  @override
  String toString() => 'ApiException($status, $detail)';
}

String _detailText(dynamic d) {
  if (d is String) return d;
  if (d is List) {
    return d.map((x) {
      if (x is Map) {
        final loc = x['loc'];
        final where = loc is List && loc.length > 1 ? '${loc.sublist(1).join('.')}: ' : '';
        return '$where${x['msg'] ?? ''}';
      }
      return '$x';
    }).join('; ');
  }
  return '';
}

/// Клиент REST API сервера Noctis (те же запросы, что делает веб-кабинет сайта).
/// При ответе 401 один раз обновляет токен; если не вышло — сообщает [onSessionExpired].
class ApiClient {
  ApiClient({required this.baseUrl, required this.tokens, http.Client? client, this.onSessionExpired}) : _http = client ?? http.Client();

  String baseUrl;
  final TokenStore tokens;
  final http.Client _http;
  void Function()? onSessionExpired;
  Future<void>? _refreshing;

  String get _base => baseUrl.replaceAll(RegExp(r'/+$'), '');

  /// Проверка соединения: отвечает ли сервер по адресу [url] (GET /health, без входа).
  Future<bool> ping(String url) async {
    final uri = Uri.tryParse('${url.trim().replaceAll(RegExp(r'/+$'), '')}/health');
    if (uri == null) return false;
    try {
      final res = await _http.get(uri).timeout(const Duration(seconds: 5));
      return res.statusCode == 200;
    } on Exception {
      return false;
    }
  }

  Future<dynamic> _send(String method, String path, {Object? json, Uint8List? bytes, bool auth = true, Map<String, String>? query}) async {
    final uri = Uri.parse('$_base$path').replace(queryParameters: query);
    final headers = <String, String>{};
    final access = auth ? await tokens.accessToken() : null;
    if (access != null) headers['Authorization'] = 'Bearer $access';
    Object? body;
    if (bytes != null) {
      headers['Content-Type'] = 'application/octet-stream';
      body = bytes;
    } else if (json != null) {
      headers['Content-Type'] = 'application/json';
      body = jsonEncode(json);
    }
    final req = http.Request(method, uri)..headers.addAll(headers);
    if (body is String) req.body = body;
    if (body is Uint8List) req.bodyBytes = body;
    http.Response res;
    try {
      res = await http.Response.fromStream(await _http.send(req).timeout(const Duration(seconds: 60)));
    } on Exception {
      throw ApiException(0);
    }
    if (res.statusCode == 204) return null;
    dynamic data;
    try {
      data = jsonDecode(utf8.decode(res.bodyBytes));
    } on FormatException {
      data = null;
    }
    if (res.statusCode >= 400) {
      throw ApiException(res.statusCode, data is Map ? _detailText(data['detail']) : '');
    }
    return data;
  }

  /// Запрос с автоматическим обновлением токена.
  Future<dynamic> call(String method, String path, {Object? json, Uint8List? bytes, Map<String, String>? query}) async {
    try {
      return await _send(method, path, json: json, bytes: bytes, query: query);
    } on ApiException catch (e) {
      if (!e.isUnauthorized || await tokens.refreshToken() == null) rethrow;
      try {
        await _refresh();
      } on ApiException {
        await tokens.clear();
        onSessionExpired?.call();
        rethrow;
      }
      return _send(method, path, json: json, bytes: bytes, query: query);
    }
  }

  Future<void> _refresh() {
    return _refreshing ??= () async {
      try {
        final pair = await _send('POST', '/api/v1/auth/refresh', json: {'refresh_token': await tokens.refreshToken()}, auth: false);
        await tokens.save(pair['access_token'] as String, pair['refresh_token'] as String);
      } finally {
        _refreshing = null;
      }
    }();
  }

  // ---------------------------------------------------------------- вход
  Future<void> login(String email, String password) async {
    final pair = await _send('POST', '/api/v1/auth/login', json: {'email': email, 'password': password}, auth: false);
    await tokens.save(pair['access_token'] as String, pair['refresh_token'] as String);
  }

  Future<void> register({required String email, required String password, required String displayName, required String language}) async {
    final pair = await _send('POST', '/api/v1/auth/register',
        json: {'email': email, 'password': password, 'display_name': displayName, 'consent': true, 'language': language}, auth: false);
    await tokens.save(pair['access_token'] as String, pair['refresh_token'] as String);
  }

  Future<void> logout() async {
    final refresh = await tokens.refreshToken();
    try {
      if (refresh != null) await _send('POST', '/api/v1/auth/logout', json: {'refresh_token': refresh});
    } on ApiException {
      // уже вышли
    }
    await tokens.clear();
  }

  Future<UserInfo> me() async => UserInfo.fromJson(await call('GET', '/api/v1/me') as Map<String, dynamic>);

  Future<void> deleteAccount(String password) => call('DELETE', '/api/v1/me', json: {'password': password});

  /// Все данные пользователя (JSON) — только по его запросу, из «Профиля».
  Future<String> exportMyData() async => const JsonEncoder.withIndent('  ').convert(await call('GET', '/api/v1/me/export'));

  /// Очистить историю диалогов всех своих колонок; возвращает число удалённых записей.
  Future<int> clearAllHistory() async => ((await call('DELETE', '/api/v1/me/history') as Map<String, dynamic>)['deleted'] as num).toInt();

  // ---------------------------------------------------------------- колонки
  Future<List<Device>> devices() async => [for (final d in await call('GET', '/api/v1/devices') as List) Device.fromJson(d as Map<String, dynamic>)];

  Future<CreatedDevice> createDevice(String name, String timezone) async =>
      CreatedDevice.fromJson(await call('POST', '/api/v1/devices', json: {'name': name, 'timezone': timezone}) as Map<String, dynamic>);

  Future<void> deleteDevice(String id) => call('DELETE', '/api/v1/devices/$id');

  Future<void> renameDevice(String id, String name) => call('PATCH', '/api/v1/devices/$id', json: {'name': name});

  Future<DeviceSettings> patchSettings(String id, Map<String, Object?> changes) async =>
      DeviceSettings.fromJson(await call('PATCH', '/api/v1/devices/$id/settings', json: changes) as Map<String, dynamic>);

  Future<void> reboot(String id) => call('POST', '/api/v1/devices/$id/reboot');

  /// Смена сети Wi‑Fi у колонки, которая на связи (при неудаче колонка сама возвращается в прежнюю сеть).
  Future<WifiStatus> changeWifi(String id, String ssid, String password) async =>
      WifiStatus.fromJson(await call('POST', '/api/v1/devices/$id/wifi', json: {'ssid': ssid, 'password': password}) as Map<String, dynamic>);

  Future<WifiStatus> wifiStatus(String id) async => WifiStatus.fromJson(await call('GET', '/api/v1/devices/$id/wifi') as Map<String, dynamic>);

  Future<Catalog> catalog(String language) async {
    final data = await _send('GET', '/api/v1/personalization/catalog', auth: false, query: {'language': language});
    return Catalog.fromJson(data as Map<String, dynamic>);
  }

  // ---------------------------------------------------------------- таймеры и будильники
  Future<List<TimerItem>> timers(String id) async => [for (final t in await call('GET', '/api/v1/devices/$id/timers') as List) TimerItem.fromJson(t as Map<String, dynamic>)];

  Future<void> addTimer(String id, int seconds, {String label = ''}) => call('POST', '/api/v1/devices/$id/timers', json: {'seconds': seconds, 'label': label});

  Future<void> timerAction(String id, int timerId, String action) => call('POST', '/api/v1/devices/$id/timers/$timerId/$action');

  Future<void> deleteTimer(String id, int timerId) => call('DELETE', '/api/v1/devices/$id/timers/$timerId');

  Future<List<AlarmItem>> alarms(String id) async => [for (final a in await call('GET', '/api/v1/devices/$id/alarms') as List) AlarmItem.fromJson(a as Map<String, dynamic>)];

  Future<void> addAlarm(String id, {required int hour, required int minute, required List<int> weekdays, String label = '', String melody = 'default'}) =>
      call('POST', '/api/v1/devices/$id/alarms', json: {'hour': hour, 'minute': minute, 'weekdays': weekdays, 'label': label, 'melody': melody});

  Future<void> setAlarmEnabled(String id, int alarmId, bool enabled) => call('PATCH', '/api/v1/devices/$id/alarms/$alarmId', json: {'enabled': enabled});

  Future<void> deleteAlarm(String id, int alarmId) => call('DELETE', '/api/v1/devices/$id/alarms/$alarmId');

  // ---------------------------------------------------------------- история
  Future<List<HistoryItem>> history(String id) async =>
      [for (final h in await call('GET', '/api/v1/devices/$id/history', query: {'limit': '50'}) as List) HistoryItem.fromJson(h as Map<String, dynamic>)];

  Future<void> clearHistory(String id) => call('DELETE', '/api/v1/devices/$id/history');

  // ---------------------------------------------------------------- музыка
  /// source: local | radio | jamendo
  Future<List<Track>> tracks(String source, String query) async {
    final List<dynamic> data;
    if (source == 'jamendo') {
      data = await call('GET', '/api/v1/media/jamendo/search', query: {'q': query}) as List;
    } else if (source == 'radio') {
      data = await call('GET', '/api/v1/media/radio') as List;
    } else {
      data = await call('GET', '/api/v1/media/tracks', query: {'source': 'local', 'q': query}) as List;
    }
    final all = [for (final t in data) Track.fromJson(t as Map<String, dynamic>)];
    if (source == 'radio' && query.isNotEmpty) {
      return all.where((t) => t.title.toLowerCase().contains(query.toLowerCase())).toList();
    }
    return all;
  }

  Future<void> addStation(String name, String url) => call('POST', '/api/v1/media/radio', json: {'name': name, 'url': url});

  Future<void> deleteTrack(Track t) => call('DELETE', t.source == 'radio' ? '/api/v1/media/radio/${t.id}' : '/api/v1/media/tracks/${t.id}');

  Future<void> uploadTrack(String filename, Uint8List bytes) => call('PUT', '/api/v1/media/library/${Uri.encodeComponent(filename)}', bytes: bytes);

  Future<PlayerInfo> player(String id) async => PlayerInfo.fromJson(await call('GET', '/api/v1/devices/$id/player') as Map<String, dynamic>);

  Future<PlayerInfo> play(String id, int trackId) async =>
      PlayerInfo.fromJson(await call('POST', '/api/v1/devices/$id/player/play', json: {'track_id': trackId}) as Map<String, dynamic>);

  /// action: pause | resume | stop | next | prev
  Future<PlayerInfo> playerAction(String id, String action) async => PlayerInfo.fromJson(await call('POST', '/api/v1/devices/$id/player/$action') as Map<String, dynamic>);
}
