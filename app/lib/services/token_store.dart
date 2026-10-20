import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Хранилище токенов входа.
abstract class TokenStore {
  Future<String?> accessToken();
  Future<String?> refreshToken();
  Future<void> save(String access, String refresh);
  Future<void> clear();
}

/// Защищённое хранилище телефона (Android Keystore / iOS Keychain).
class SecureTokenStore implements TokenStore {
  SecureTokenStore([FlutterSecureStorage? storage]) : _s = storage ?? const FlutterSecureStorage();
  final FlutterSecureStorage _s;

  @override
  Future<String?> accessToken() => _s.read(key: 'noctis_access');

  @override
  Future<String?> refreshToken() => _s.read(key: 'noctis_refresh');

  @override
  Future<void> save(String access, String refresh) async {
    await _s.write(key: 'noctis_access', value: access);
    await _s.write(key: 'noctis_refresh', value: refresh);
  }

  @override
  Future<void> clear() async {
    await _s.delete(key: 'noctis_access');
    await _s.delete(key: 'noctis_refresh');
  }
}

/// В памяти — для тестов.
class MemoryTokenStore implements TokenStore {
  String? _a;
  String? _r;

  @override
  Future<String?> accessToken() async => _a;

  @override
  Future<String?> refreshToken() async => _r;

  @override
  Future<void> save(String access, String refresh) async {
    _a = access;
    _r = refresh;
  }

  @override
  Future<void> clear() async {
    _a = null;
    _r = null;
  }
}
