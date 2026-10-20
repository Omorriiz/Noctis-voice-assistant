import 'dart:async';

/// Найденная по Bluetooth колонка (имя вида NOCTIS_A1B2C3).
class FoundSpeaker {
  FoundSpeaker({required this.id, required this.name, this.rssi = 0});
  final String id;
  final String name;
  final int rssi;
}

/// Ошибка сопряжения. [wrongCode] — не подошёл код с экрана колонки.
class PairingException implements Exception {
  PairingException(this.message, {this.wrongCode = false, this.permissionDenied = false});
  final String message;
  final bool wrongCode;
  final bool permissionDenied;

  @override
  String toString() => 'PairingException($message)';
}

/// Канал сопряжения с колонкой. Настоящая реализация — по Bluetooth (ble_pairing.dart),
/// для тестов, веб-версии и показа без платы — [MockPairing].
abstract class PairingTransport {
  /// true — доступ к Bluetooth есть.
  Future<bool> ensurePermissions();

  Future<List<FoundSpeaker>> scan({Duration timeout = const Duration(seconds: 8)});

  /// Устанавливает защищённый сеанс (код с экрана колонки). Неверный код → [PairingException.wrongCode].
  Future<void> connect(FoundSpeaker speaker, String code);

  /// Передаёт колонке адрес сервера и учётные данные устройства (endpoint `noctis-config`). Делается до Wi‑Fi.
  /// При повторной настройке [deviceId] и [token] не передаются: колонка сохраняет свои (секрет известен только ей).
  Future<void> sendLink({required String server, required bool tls, String? deviceId, String? token});

  /// Передаёт Wi‑Fi и запускает подключение. Колонка закрывает Bluetooth, когда вышла в сеть.
  Future<void> sendWifi(String ssid, String password);

  Future<void> close();
}

/// Адрес сервера в виде host:port, который принимает колонка, и признак TLS (из http/https/ws/wss-адреса).
({String server, bool tls}) serverForBoard(String url) {
  var s = url.trim();
  var tls = false;
  for (final p in const [('https://', true), ('http://', false), ('wss://', true), ('ws://', false)]) {
    if (s.toLowerCase().startsWith(p.$1)) {
      s = s.substring(p.$1.length);
      tls = p.$2;
      break;
    }
  }
  s = s.split('/').first;
  return (server: s, tls: tls);
}

/// Заглушка: колонка «найдена» сразу, код 00000000 считается неверным. Bluetooth не используется.
class MockPairing implements PairingTransport {
  MockPairing({this.delay = const Duration(milliseconds: 400)});
  final Duration delay;
  Map<String, Object?>? lastLink;
  ({String ssid, String password})? lastWifi;
  bool closed = false;

  @override
  Future<bool> ensurePermissions() async => true;

  @override
  Future<List<FoundSpeaker>> scan({Duration timeout = const Duration(seconds: 8)}) async {
    await Future<void>.delayed(delay);
    return [FoundSpeaker(id: 'mock-1', name: 'NOCTIS_A1B2C3', rssi: -55)];
  }

  @override
  Future<void> connect(FoundSpeaker speaker, String code) async {
    await Future<void>.delayed(delay);
    if (!RegExp(r'^\d{8}$').hasMatch(code) || code == '00000000') {
      throw PairingException('wrong code', wrongCode: true);
    }
  }

  @override
  Future<void> sendLink({required String server, required bool tls, String? deviceId, String? token}) async {
    await Future<void>.delayed(delay);
    lastLink = {'server': server, 'tls': tls, 'device_id': ?deviceId, 'token': ?token};
  }

  @override
  Future<void> sendWifi(String ssid, String password) async {
    await Future<void>.delayed(delay);
    lastWifi = (ssid: ssid, password: password);
  }

  @override
  Future<void> close() async {
    closed = true;
  }
}
