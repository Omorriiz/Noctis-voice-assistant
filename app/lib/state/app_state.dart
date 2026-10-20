import 'package:flutter/material.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../config.dart';
import '../l10n/l10n.dart';
import '../models/models.dart';
import '../services/api_client.dart';
import '../services/pairing.dart';
import '../services/token_store.dart';

enum AuthStatus { unknown, signedOut, signedIn }

/// Состояние приложения: вход, колонки, язык, тема.
class AppState extends ChangeNotifier {
  AppState({required this.api, required this.prefs, required this.tokens, required this.pairing, String? systemLanguage, Future<String> Function()? localTimezone})
      : localTimezone = localTimezone ?? _deviceTimezone {
    lang = prefs.getString('lang') ?? pickLanguage(systemLanguage);
    themeMode = prefs.getString('theme') == 'light' ? ThemeMode.light : ThemeMode.dark;
    api.onSessionExpired = _sessionExpired;
  }

  final ApiClient api;
  final SharedPreferences prefs;
  final TokenStore tokens;
  final PairingTransport pairing;

  /// Часовой пояс телефона (название IANA). Если определить не удалось за 2 секунды — Europe/Moscow (значение сервера по умолчанию).
  final Future<String> Function() localTimezone;

  static Future<String> _deviceTimezone() async {
    try {
      return (await FlutterTimezone.getLocalTimezone().timeout(const Duration(seconds: 2))).identifier;
    } catch (_) {
      return 'Europe/Moscow';
    }
  }

  late String lang;
  late ThemeMode themeMode;
  AuthStatus status = AuthStatus.unknown;
  UserInfo? user;
  List<Device> devices = [];
  String? deviceId;
  Catalog? catalog;

  Strings get strings => Strings(lang);

  Device? get device {
    for (final d in devices) {
      if (d.publicId == deviceId) return d;
    }
    return devices.isEmpty ? null : devices.first;
  }

  /// Запуск: если токены есть — загрузить данные, иначе экран входа.
  Future<void> start() async {
    if (await tokens.accessToken() == null) {
      status = AuthStatus.signedOut;
      notifyListeners();
      return;
    }
    try {
      await _loadAll();
      status = AuthStatus.signedIn;
    } on ApiException catch (e) {
      status = AuthStatus.signedOut;
      if (!e.isUnauthorized) lastError = e;
    }
    notifyListeners();
  }

  Object? lastError;

  Future<void> _loadAll() async {
    final res = await Future.wait([api.me(), api.devices(), api.catalog(lang)]);
    user = res[0] as UserInfo;
    devices = res[1] as List<Device>;
    catalog = res[2] as Catalog;
    deviceId = prefs.getString('device');
    if (device != null) deviceId = device!.publicId;
  }

  Future<void> login(String email, String password) async {
    await api.login(email, password);
    await _loadAll();
    status = AuthStatus.signedIn;
    notifyListeners();
  }

  Future<void> register(String email, String password, String name) async {
    await api.register(email: email, password: password, displayName: name, language: lang);
    await _loadAll();
    status = AuthStatus.signedIn;
    notifyListeners();
  }

  Future<void> logout() async {
    await api.logout();
    _clear();
  }

  Future<void> deleteAccount(String password) async {
    await api.deleteAccount(password);
    await tokens.clear();
    _clear();
  }

  void _clear() {
    user = null;
    devices = [];
    deviceId = null;
    status = AuthStatus.signedOut;
    notifyListeners();
  }

  void _sessionExpired() {
    _clear();
  }

  Future<void> refreshDevices() async {
    devices = await api.devices();
    if (device != null) deviceId = device!.publicId;
    notifyListeners();
  }

  void selectDevice(String id) {
    deviceId = id;
    prefs.setString('device', id);
    notifyListeners();
  }

  Future<void> setLanguage(String code) async {
    if (!kLanguages.contains(code)) return;
    lang = code;
    await prefs.setString('lang', code);
    notifyListeners();
    try {
      catalog = await api.catalog(code);
      notifyListeners();
    } on ApiException {
      // названия обновятся при следующем входе
    }
  }

  Future<void> setThemeMode(ThemeMode mode) async {
    themeMode = mode == ThemeMode.light ? ThemeMode.light : ThemeMode.dark;
    await prefs.setString('theme', themeMode == ThemeMode.light ? 'light' : 'dark');
    notifyListeners();
  }

  /// Как часто опрашивать итог смены Wi‑Fi (в тестах ставится короче).
  Duration wifiPollInterval = const Duration(seconds: 2);

  /// Адрес сервера должен начинаться с http:// или https://.
  static bool validServerUrl(String url) => RegExp(r'^https?://[^\s/]+', caseSensitive: false).hasMatch(url.trim());

  /// Скрытое окно проверки: сменить адрес сервера (сохраняется на телефоне).
  Future<void> setServer(String url) async {
    api.baseUrl = url.trim();
    await prefs.setString('server', api.baseUrl);
    notifyListeners();
  }

  /// Вернуть адрес сервера, заданный при сборке приложения.
  Future<void> resetServer() async {
    api.baseUrl = kServerDefault;
    await prefs.remove('server');
    notifyListeners();
  }

  /// Понятный текст ошибки на текущем языке (технические подробности пользователю не показываются).
  String errorText(Object e) {
    final s = strings;
    if (e is ApiException) {
      return switch (e.status) {
        0 => s.t('err_network', {'server': ''}),
        401 => s.t('err_401'),
        403 => s.t('err_403'),
        404 => s.t('err_404'),
        409 => s.t('err_409'),
        429 => s.t('err_429'),
        502 => s.t('err_502'),
        503 => s.t('err_503'),
        _ => s.t('err_generic'),
      };
    }
    return s.t('err_generic');
  }
}
