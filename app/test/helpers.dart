import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:noctis/services/api_client.dart';
import 'package:noctis/services/pairing.dart';
import 'package:noctis/services/token_store.dart';
import 'package:noctis/state/app_state.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Поддельный сервер Noctis: отвечает на запросы приложения так же, как настоящий (формат — server/noctis_server/schemas.py).
class FakeServer {
  final List<String> requests = [];
  bool accessExpired = false; // следующий запрос с токеном вернёт 401
  bool refreshFails = false;
  bool offline = false; // нет связи
  bool deviceOnline = true;
  bool wrongPassword = false;
  String wifiOutcome = 'ok'; // чем закончится смена сети: ok | failed | timeout
  bool _wifiPending = false;
  String _wifiSsid = '';
  List<Map<String, dynamic>> devices = [];
  List<Map<String, dynamic>> timers = [];
  List<Map<String, dynamic>> alarms = [];
  int _id = 1;
  int polls = 0;

  static Map<String, dynamic> deviceJson(String id, String name, {bool online = true, String tz = 'Europe/Moscow'}) => {
        'public_id': id,
        'name': name,
        'firmware_version': '0.4.1',
        'last_seen': '2026-10-17T12:00:00',
        'online': online,
        'enabled': true,
        'settings': {'wake_word': 'nils', 'voice_gender': 'male', 'character_id': 'friendly', 'language': 'ru', 'volume': 70, 'timezone': tz, 'revision': 1},
      };

  MockClient get client => MockClient((req) async {
        requests.add('${req.method} ${req.url.path}');
        if (offline) throw http.ClientException('offline');
        final path = req.url.path;
        final body = req.body.isEmpty ? <String, dynamic>{} : jsonDecode(req.body);
        http.Response ok(Object? data, [int code = 200]) => http.Response(code == 204 ? '' : jsonEncode(data), code, headers: {'content-type': 'application/json; charset=utf-8'});
        http.Response err(int code, String detail) => ok({'detail': detail}, code);

        if (path == '/health') return ok({'status': 'ok'});
        if (path == '/api/v1/auth/login') {
          return wrongPassword ? err(401, 'Invalid email or password') : ok({'access_token': 'a1', 'refresh_token': 'r1', 'token_type': 'bearer', 'expires_in': 900});
        }
        if (path == '/api/v1/auth/register') {
          if (body['email'] == 'taken@example.com') return err(409, 'Email already registered');
          return ok({'access_token': 'a1', 'refresh_token': 'r1', 'token_type': 'bearer', 'expires_in': 900}, 201);
        }
        if (path == '/api/v1/auth/refresh') {
          return refreshFails ? err(401, 'bad refresh') : ok({'access_token': 'a2', 'refresh_token': 'r2', 'token_type': 'bearer', 'expires_in': 900});
        }
        if (path == '/api/v1/auth/logout') return ok(null, 204);
        if (path == '/api/v1/personalization/catalog') {
          return ok({
            'languages': [for (final l in ['ru', 'en', 'es', 'de', 'fr']) {'id': l, 'name': l.toUpperCase()}],
            'characters': [
              {'id': 'friendly', 'name': 'Дружелюбный', 'description': 'Тёплый и добрый.'},
              {'id': 'formal', 'name': 'Официальный', 'description': 'Вежливый.'}
            ],
            'wake_words': [
              {'id': 'nils', 'label': 'Нильс / Nils', 'model_ready': true},
              {'id': 'alexa', 'label': 'Alexa', 'model_ready': true}
            ],
          });
        }
        // дальше нужен токен
        final auth = req.headers['Authorization'];
        if (auth == null) return err(401, 'Not authenticated');
        if (accessExpired && auth == 'Bearer a1') return err(401, 'expired');

        if (path == '/api/v1/me/export' && req.method == 'GET') return ok({'account': {'email': 'anna@example.com'}, 'devices': devices});
        if (path == '/api/v1/me/history' && req.method == 'DELETE') return ok({'deleted': 3});
        if (path == '/api/v1/me') {
          if (req.method == 'DELETE') return ok(null, 204);
          return ok({'id': 1, 'email': 'anna@example.com', 'display_name': 'Anna', 'role': 'user', 'created_at': '2026-10-01T00:00:00'});
        }
        if (path == '/api/v1/devices' && req.method == 'GET') return ok(devices);
        if (path == '/api/v1/devices' && req.method == 'POST') {
          final id = 'dev${_id++}';
          final d = deviceJson(id, body['name'] as String, online: deviceOnline, tz: body['timezone'] as String);
          devices.add(d);
          return ok({...d, 'device_token': 'tok-$id'}, 201);
        }
        final m = RegExp(r'^/api/v1/devices/([^/]+)(/.*)?$').firstMatch(path);
        if (m != null) {
          final id = m.group(1)!, rest = m.group(2) ?? '';
          final idx = devices.indexWhere((d) => d['public_id'] == id);
          if (idx < 0) return err(404, 'Device not found');
          if (rest.isEmpty) {
            if (req.method == 'DELETE') {
              devices.removeAt(idx);
              return ok(null, 204);
            }
            if (req.method == 'PATCH') {
              devices[idx] = {...devices[idx], 'name': body['name']};
              return ok(devices[idx]);
            }
          }
          if (rest == '/settings' && req.method == 'PATCH') {
            final st = {...(devices[idx]['settings'] as Map<String, dynamic>), ...body};
            devices[idx] = {...devices[idx], 'settings': st};
            return ok(st);
          }
          if (rest == '/wifi' && req.method == 'POST') {
            final pw = (body['password'] ?? '') as String;
            if (pw.isNotEmpty && pw.length < 8) return err(422, 'password: too short');
            if (_wifiPending) return err(409, 'A Wi-Fi change is already in progress');
            _wifiPending = true;
            _wifiSsid = body['ssid'] as String;
            return ok({'state': 'pending', 'ssid': _wifiSsid, 'error': '', 'request_id': 'r1'}, 202);
          }
          if (rest == '/wifi' && req.method == 'GET') {
            if (_wifiPending && polls++ >= 1) {
              _wifiPending = false;
              return ok({'state': wifiOutcome, 'ssid': _wifiSsid, 'error': wifiOutcome == 'failed' ? 'connect_failed' : '', 'request_id': 'r1'});
            }
            return ok({'state': _wifiPending ? 'pending' : 'none', 'ssid': _wifiSsid, 'error': '', 'request_id': 'r1'});
          }
          if (rest == '/timers' && req.method == 'GET') return ok(timers);
          if (rest == '/timers' && req.method == 'POST') {
            final t = {'id': _id++, 'label': body['label'], 'duration_sec': body['seconds'], 'remaining_sec': body['seconds'], 'state': 'running'};
            timers.add(t);
            return ok(t, 201);
          }
          final tm = RegExp(r'^/timers/(\d+)(?:/(pause|resume))?$').firstMatch(rest);
          if (tm != null) {
            final i = timers.indexWhere((t) => t['id'] == int.parse(tm.group(1)!));
            if (req.method == 'DELETE') {
              timers.removeAt(i);
              return ok(null, 204);
            }
            timers[i] = {...timers[i], 'state': tm.group(2) == 'pause' ? 'paused' : 'running'};
            return ok(timers[i]);
          }
          if (rest == '/alarms' && req.method == 'GET') return ok(alarms);
          if (rest == '/alarms' && req.method == 'POST') {
            final a = {'id': _id++, 'hour': body['hour'], 'minute': body['minute'], 'weekdays': body['weekdays'], 'date': null, 'label': body['label'], 'melody': body['melody'], 'enabled': true};
            alarms.add(a);
            return ok(a, 201);
          }
          if (rest == '/history') {
            if (req.method == 'DELETE') return ok(null, 204);
            return ok([
              {'id': 1, 'created_at': '2026-10-17T10:00:00', 'user_text': 'Который час?', 'assistant_text': 'Десять часов.', 'language': 'ru'}
            ]);
          }
          if (rest == '/player') return ok({'state': 'stopped', 'index': 0, 'total': 0, 'track': null});
        }
        if (path == '/api/v1/media/tracks') {
          return ok([
            {'id': 5, 'title': 'Тестовый трек', 'artist': 'Автор', 'album': '', 'genre': '', 'source': 'local', 'duration_sec': 30.0, 'license': ''}
          ]);
        }
        if (path == '/api/v1/media/radio') return ok([]);
        return err(404, 'Not found: $path');
      });
}

class TestEnv {
  TestEnv({this.pairing, FakeServer? server}) : server = server ?? FakeServer();

  final FakeServer server;
  PairingTransport? pairing;
  late final MemoryTokenStore tokens;
  late final ApiClient api;
  late final AppState state;

  static Future<TestEnv> create({FakeServer? server, PairingTransport? pairing, String systemLanguage = 'ru'}) async {
    SharedPreferences.setMockInitialValues({});
    final env = TestEnv(server: server, pairing: pairing);
    env.tokens = MemoryTokenStore();
    env.api = ApiClient(baseUrl: 'http://test.local:8000', tokens: env.tokens, client: env.server.client);
    env.state = AppState(api: env.api, prefs: await SharedPreferences.getInstance(), tokens: env.tokens, pairing: env.pairing ?? MockPairing(delay: Duration.zero), systemLanguage: systemLanguage, localTimezone: () async => 'Europe/Berlin');
    return env;
  }
}
