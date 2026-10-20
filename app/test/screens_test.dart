import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:noctis/l10n/l10n.dart';
import 'package:noctis/models/models.dart';
import 'package:noctis/main.dart';
import 'package:noctis/screens/pairing_screen.dart';
import 'package:noctis/services/pairing.dart';
import 'package:noctis/state/app_state.dart';
import 'package:noctis/theme/noctis_theme.dart';
import 'package:noctis/widgets/widgets.dart';
import 'package:provider/provider.dart';

import 'helpers.dart';

Strings get ru => const Strings('ru');
Strings get en => const Strings('en');

void bigScreen(WidgetTester tester) {
  tester.view.physicalSize = const Size(900, 2000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
}

Future<void> settle(WidgetTester tester, [int steps = 8]) async {
  for (var i = 0; i < steps; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Future<TestEnv> signedIn({FakeServer? server, PairingTransport? pairing, String lang = 'ru'}) async {
  final env = await TestEnv.create(server: server, pairing: pairing, systemLanguage: lang);
  await env.state.login('anna@example.com', 'secret123');
  return env;
}

void main() {
  testWidgets('экран входа: русский по умолчанию, регистрация требует согласие', (tester) async {
    bigScreen(tester);
    final env = await TestEnv.create();
    await env.state.start();
    await tester.pumpWidget(NoctisApp(state: env.state));
    await settle(tester);

    expect(find.text(ru.t('tab_login')), findsWidgets);
    expect(find.text(ru.t('login')), findsOneWidget);

    await tester.tap(find.text(ru.t('tab_register')));
    await settle(tester);
    expect(find.text(ru.t('consent')), findsOneWidget);
    final submit = find.widgetWithText(InkWell, ru.t('create_account'));
    expect(submit, findsWidgets);
  });

  testWidgets('смена языка и темы', (tester) async {
    bigScreen(tester);
    final env = await TestEnv.create();
    await env.state.start();
    await tester.pumpWidget(NoctisApp(state: env.state));
    await settle(tester);

    await env.state.setLanguage('en');
    await settle(tester);
    expect(find.text(en.t('tab_register')), findsOneWidget);

    expect(env.state.themeMode, ThemeMode.dark, reason: 'тёмная тема по умолчанию, как на сайте');
    await env.state.setThemeMode(ThemeMode.light);
    await settle(tester);
    expect(Theme.of(tester.element(find.byType(Scaffold).first)).brightness, Brightness.light);
  });

  testWidgets('вход: неверный пароль показывает понятную ошибку', (tester) async {
    bigScreen(tester);
    final env = await TestEnv.create(server: FakeServer()..wrongPassword = true);
    await env.state.start();
    await tester.pumpWidget(NoctisApp(state: env.state));
    await settle(tester);

    await tester.enterText(find.byType(TextFormField).at(0), 'anna@example.com');
    await tester.enterText(find.byType(TextFormField).at(1), 'wrongpass');
    await tester.tap(find.text(ru.t('login')));
    await settle(tester, 12);
    expect(find.text(ru.t('err_401')), findsOneWidget);
    expect(env.state.status, AuthStatus.signedOut);
  });

  testWidgets('нет связи с сервером: сообщение без технических подробностей', (tester) async {
    bigScreen(tester);
    final env = await TestEnv.create(server: FakeServer()..offline = true);
    await env.state.start();
    await tester.pumpWidget(NoctisApp(state: env.state));
    await settle(tester);
    await tester.enterText(find.byType(TextFormField).at(0), 'anna@example.com');
    await tester.enterText(find.byType(TextFormField).at(1), 'secret123');
    await tester.tap(find.text(ru.t('login')));
    await settle(tester, 12);
    final text = ru.t('err_network');
    expect(find.text(text), findsOneWidget);
    expect(text.contains('NOCTIS_'), isFalse);
    expect(text.contains('http'), isFalse);
  });

  testWidgets('главный экран: колонка, разделы, быстрый таймер', (tester) async {
    bigScreen(tester);
    final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Гостиная')];
    final env = await signedIn(server: server);
    await tester.pumpWidget(NoctisApp(state: env.state));
    await settle(tester);

    expect(find.text('Гостиная'), findsWidgets);
    expect(find.text(ru.t('online')), findsOneWidget);
    for (final k in ['tab_speaker', 'tab_timers_short', 'tab_music', 'tab_history', 'tab_profile']) {
      expect(find.text(ru.t(k)), findsWidgets, reason: k);
    }

    await tester.tap(find.text(ru.t('tab_timers_short')));
    await settle(tester, 12);
    expect(find.text(ru.t('timers_title')), findsNWidgets(2), reason: 'заголовок раздела и подпись в нижней панели');
    await tester.tap(find.text('5 ${ru.t('min_short')}'));
    await settle(tester, 12);
    expect(server.timers.length, 1);
    expect(server.timers.single['duration_sec'], 300);
    expect(find.text('5:00'), findsWidgets);

    await tester.tap(find.text(ru.t('tab_history')));
    await settle(tester, 12);
    expect(find.textContaining('Который час?'), findsOneWidget);
  });

  testWidgets('колонка не в сети: предупреждение и пояснение', (tester) async {
    bigScreen(tester);
    final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Кухня', online: false)];
    final env = await signedIn(server: server);
    await tester.pumpWidget(NoctisApp(state: env.state));
    await settle(tester);
    expect(find.text(ru.t('offline_note')), findsOneWidget);
    expect(find.text(ru.t('offline')), findsOneWidget);
  });

  testWidgets('нет колонок: предложение добавить', (tester) async {
    bigScreen(tester);
    final env = await signedIn();
    await tester.pumpWidget(NoctisApp(state: env.state));
    await settle(tester);
    expect(find.text(ru.t('no_devices')), findsOneWidget);
    expect(find.text(ru.t('add_speaker')), findsWidgets);
  });

  group('мастер сопряжения (на заглушке Bluetooth)', () {
    Widget host(TestEnv env) => ChangeNotifierProvider<AppState>.value(
          value: env.state,
          child: Consumer<AppState>(builder: (c, app, _) => MaterialApp(theme: NoctisTheme.build(dark: true), home: const PairingScreen())),
        );

    Future<void> goToWifi(WidgetTester tester, {String code = '12345678'}) async {
      await tester.tap(find.text(ru.t('pair_scan')));
      await settle(tester, 12);
      expect(find.text('NOCTIS_A1B2C3'), findsOneWidget);
      await tester.tap(find.text('NOCTIS_A1B2C3'));
      await settle(tester);
      await tester.enterText(find.byType(TextField), code);
      await settle(tester, 3);
      await tester.tap(find.text(ru.t('next_step')));
      await settle(tester);
      await tester.enterText(find.byType(TextField).first, 'HomeWiFi');
      await settle(tester, 3);
    }

    testWidgets('успех: колонка создана, адрес сервера и Wi‑Fi переданы, статус «онлайн»', (tester) async {
      bigScreen(tester);
      final mock = MockPairing(delay: Duration.zero);
      final server = FakeServer();
      final env = await signedIn(server: server, pairing: mock);
      await tester.pumpWidget(host(env));
      await settle(tester);
      await goToWifi(tester);

      await tester.tap(find.text(ru.t('pair_connect')));
      await settle(tester, 6);
      await tester.pump(const Duration(seconds: 3));
      await settle(tester, 6);

      expect(find.text(ru.t('pair_done')), findsOneWidget);
      expect(server.devices.length, 1);
      expect(mock.lastLink!['server'], 'test.local:8000');
      expect(mock.lastLink!['tls'], false);
      expect(mock.lastLink!['device_id'], server.devices.single['public_id']);
      expect(mock.lastLink!['token'], 'tok-${server.devices.single['public_id']}');
      expect(mock.lastWifi!.ssid, 'HomeWiFi');
      expect(server.devices.single['settings']['timezone'], 'Europe/Berlin');
      expect(env.state.deviceId, server.devices.single['public_id']);
    });

    testWidgets('неверный код: возвращает к вводу кода, устройство на сервере не создаётся', (tester) async {
      bigScreen(tester);
      final server = FakeServer();
      final env = await signedIn(server: server, pairing: MockPairing(delay: Duration.zero));
      await tester.pumpWidget(host(env));
      await settle(tester);
      await goToWifi(tester, code: '00000000');

      await tester.tap(find.text(ru.t('pair_connect')));
      await settle(tester, 10);

      expect(find.text(ru.t('pair_wrong_code')), findsOneWidget);
      expect(find.text(ru.t('pair_code_title')), findsOneWidget);
      expect(server.devices, isEmpty);
    });

    testWidgets('ошибка Wi‑Fi после передачи личности: устройство остаётся (иначе плата получит 403)', (tester) async {
      bigScreen(tester);
      final server = FakeServer();
      final env = await signedIn(server: server, pairing: _WifiFails());
      await tester.pumpWidget(host(env));
      await settle(tester);
      await goToWifi(tester);

      await tester.tap(find.text(ru.t('pair_connect')));
      await settle(tester, 12);

      expect(find.textContaining(ru.t('pair_failed')), findsOneWidget);
      expect(server.devices, hasLength(1), reason: 'плата уже хранит токен этого устройства');
    });

    testWidgets('ошибка до передачи личности: созданное устройство удаляется', (tester) async {
      bigScreen(tester);
      final server = FakeServer();
      final env = await signedIn(server: server, pairing: _LinkFails());
      await tester.pumpWidget(host(env));
      await settle(tester);
      await goToWifi(tester);

      await tester.tap(find.text(ru.t('pair_connect')));
      await settle(tester, 12);

      expect(find.textContaining(ru.t('pair_failed')), findsOneWidget);
      expect(server.devices, isEmpty, reason: 'устройство-«сирота» не должно остаться');
    });
  });

  group('домашний город', () {
    testWidgets('поле есть, значение уходит на сервер и возвращается при следующем открытии', (tester) async {
      bigScreen(tester);
      final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Кухня')];
      final env = await signedIn(server: server);
      await env.state.start();
      await tester.pumpWidget(NoctisApp(state: env.state));
      await settle(tester);
      await tester.dragUntilVisible(find.text(ru.t('home_city')), find.byType(Scrollable).first, const Offset(0, -200));
      await settle(tester, 3);
      expect(find.text(ru.t('home_city_hint')), findsOneWidget);
      final field = find.descendant(of: find.ancestor(of: find.text(ru.t('home_city')), matching: find.byType(LabeledField)), matching: find.byType(TextField));
      await tester.enterText(field, '  Казань ');
      await settle(tester, 2);
      await tester.dragUntilVisible(find.text(ru.t('save')), find.byType(Scrollable).first, const Offset(0, -200));
      await tester.tap(find.text(ru.t('save')));
      await settle(tester, 6);
      expect((server.devices.single['settings'] as Map)['home_city'], 'Казань');
      expect(env.state.device!.settings.homeCity, 'Казань');
    });
  });

  group('мои данные', () {
    testWidgets('профиль: выгрузка копируется в буфер, очистка истории спрашивает подтверждение', (tester) async {
      bigScreen(tester);
      final copied = <String>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(SystemChannels.platform, (call) async {
        if (call.method == 'Clipboard.setData') copied.add((call.arguments as Map)['text'] as String);
        return null;
      });
      addTearDown(() => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(SystemChannels.platform, null));
      final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Кухня')];
      final env = await signedIn(server: server);
      await env.state.start();
      await tester.pumpWidget(NoctisApp(state: env.state));
      await settle(tester);
      await tester.tap(find.text(ru.t('tab_profile')).last);
      await settle(tester, 3);
      expect(find.text(ru.t('priv_intro')), findsOneWidget);

      await tester.tap(find.text(ru.t('priv_export_app')));
      await settle(tester, 4);
      expect(server.requests, contains('GET /api/v1/me/export'));
      expect(copied.single, contains('anna@example.com'));
      expect(find.text(ru.t('priv_copied')), findsOneWidget);

      await tester.tap(find.text(ru.t('priv_clear')));
      await settle(tester, 3);
      await tester.tap(find.text(ru.t('cancel')));
      await settle(tester, 3);
      expect(server.requests.where((r) => r.startsWith('DELETE /api/v1/me/history')), isEmpty);
      await tester.tap(find.text(ru.t('priv_clear')));
      await settle(tester, 3);
      await tester.tap(find.descendant(of: find.byType(AlertDialog), matching: find.text(ru.t('priv_clear'))));
      await settle(tester, 4);
      expect(server.requests, contains('DELETE /api/v1/me/history'));
    });
  });

  group('удаление колонки', () {
    Future<void> openSpeaker(WidgetTester tester, TestEnv env) async {
      await tester.pumpWidget(NoctisApp(state: env.state));
      await settle(tester);
      await tester.dragUntilVisible(find.text(ru.t('dev_delete')), find.byType(Scrollable).first, const Offset(0, -200));
      await settle(tester, 3);
    }

    testWidgets('кнопка есть; без подтверждения ничего не удаляется; с подтверждением колонка исчезает', (tester) async {
      bigScreen(tester);
      final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Кухня')];
      final env = await signedIn(server: server);
      await env.state.start();
      await openSpeaker(tester, env);

      await tester.tap(find.text(ru.t('dev_delete')));
      await settle(tester, 3);
      expect(find.textContaining('Кухня'), findsWidgets);   // в вопросе названа колонка
      await tester.tap(find.text(ru.t('cancel')));
      await settle(tester, 3);
      expect(server.devices, hasLength(1), reason: 'отмена не удаляет');
      expect(server.requests.where((r) => r.startsWith('DELETE /api/v1/devices')), isEmpty);

      await tester.tap(find.text(ru.t('dev_delete')));
      await settle(tester, 3);
      await tester.tap(find.descendant(of: find.byType(AlertDialog), matching: find.text(ru.t('dev_delete'))));
      await settle(tester, 6);
      expect(server.requests, contains('DELETE /api/v1/devices/d1'));
      expect(server.devices, isEmpty);
      expect(env.state.devices, isEmpty);
    });
  });

  group('смена Wi‑Fi', () {
    Future<void> openSpeaker(WidgetTester tester, TestEnv env) async {
      await tester.pumpWidget(NoctisApp(state: env.state));
      await settle(tester);
      await tester.dragUntilVisible(find.text(ru.t('wifi_ble_button')), find.byType(Scrollable).first, const Offset(0, -200));
      await settle(tester, 3);
    }

    Future<void> applyWifi(WidgetTester tester, {String password = 'new-password'}) async {
      await tester.tap(find.text(ru.t('wifi_apply')));
      await settle(tester);
      final fields = find.descendant(of: find.byType(AlertDialog), matching: find.byType(TextField));
      await tester.enterText(fields.at(0), 'Omorriz');
      await tester.enterText(fields.at(1), password);
      await settle(tester, 3);
      await tester.tap(find.descendant(of: find.byType(AlertDialog), matching: find.text(ru.t('wifi_apply'))));
      await settle(tester, 4);
    }

    testWidgets('сервер передал команду, колонка подтвердила новую сеть', (tester) async {
      bigScreen(tester);
      final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Кухня')];
      final env = await signedIn(server: server);
      env.state.wifiPollInterval = const Duration(milliseconds: 100);
      await env.state.start();
      await openSpeaker(tester, env);
      await applyWifi(tester);
      await tester.pump(const Duration(milliseconds: 400));
      await settle(tester, 8);
      expect(find.text(ru.t('wifi_ok', {'ssid': 'Omorriz'})), findsOneWidget);
      expect(server.requests, contains('POST /api/v1/devices/d1/wifi'));
    });

    testWidgets('новая сеть не сработала: колонка осталась в прежней, пользователь это видит', (tester) async {
      bigScreen(tester);
      final server = FakeServer()
        ..wifiOutcome = 'failed'
        ..devices = [FakeServer.deviceJson('d1', 'Кухня')];
      final env = await signedIn(server: server);
      env.state.wifiPollInterval = const Duration(milliseconds: 100);
      await env.state.start();
      await openSpeaker(tester, env);
      await applyWifi(tester);
      await tester.pump(const Duration(milliseconds: 400));
      await settle(tester, 8);
      expect(find.text(ru.t('wifi_failed', {'ssid': 'Omorriz'})), findsOneWidget);
    });

    testWidgets('слишком короткий пароль: понятное сообщение', (tester) async {
      bigScreen(tester);
      final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Кухня')];
      final env = await signedIn(server: server);
      await env.state.start();
      await openSpeaker(tester, env);
      await applyWifi(tester, password: 'short');
      await settle(tester, 6);
      expect(find.text(ru.t('wifi_invalid')), findsOneWidget);
    });

    testWidgets('колонка не в сети: смены через сервер нет, есть настройка по Bluetooth', (tester) async {
      bigScreen(tester);
      final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Кухня', online: false)];
      final env = await signedIn(server: server);
      await env.state.start();
      await openSpeaker(tester, env);
      expect(find.text(ru.t('wifi_apply')), findsNothing);
      expect(find.text(ru.t('wifi_offline_app')), findsOneWidget);
      expect(find.text(ru.t('wifi_ble_button')), findsOneWidget);
    });

    Widget host(TestEnv env, Device d) => ChangeNotifierProvider<AppState>.value(
          value: env.state,
          child: Consumer<AppState>(builder: (c, app, _) => MaterialApp(theme: NoctisTheme.build(dark: true), home: PairingScreen(reconfigure: d))),
        );

    Future<void> toWifi(WidgetTester tester) async {
      await tester.tap(find.text(ru.t('pair_scan')));
      await settle(tester, 12);
      await tester.tap(find.text('NOCTIS_A1B2C3'));
      await settle(tester);
      await tester.enterText(find.byType(TextField), '12345678');
      await settle(tester, 3);
      await tester.tap(find.text(ru.t('next_step')));
      await settle(tester);
      await tester.enterText(find.byType(TextField).first, 'Omorriz');
      await settle(tester, 3);
    }

    testWidgets('повторная настройка по Bluetooth: без создания устройства, личность платы не передаётся', (tester) async {
      bigScreen(tester);
      final mock = MockPairing(delay: Duration.zero);
      final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Кухня')];
      final env = await signedIn(server: server, pairing: mock);
      await env.state.start();
      await tester.pumpWidget(host(env, env.state.devices.single));
      await settle(tester);
      expect(find.text(ru.t('re_title')), findsOneWidget);
      expect(find.text(ru.t('pair_name')), findsNothing);
      await toWifi(tester);
      await tester.tap(find.text(ru.t('pair_connect')));
      await settle(tester, 6);
      await tester.pump(const Duration(seconds: 3));
      await settle(tester, 6);

      expect(find.text(ru.t('re_done')), findsOneWidget);
      expect(mock.lastLink!.containsKey('device_id'), isFalse);
      expect(mock.lastLink!.containsKey('token'), isFalse);
      expect(mock.lastLink!['server'], 'test.local:8000');
      expect(mock.lastWifi!.ssid, 'Omorriz');
      expect(server.devices.length, 1);
      expect(server.requests.where((r) => r == 'POST /api/v1/devices'), isEmpty);
    });

    testWidgets('ошибка при повторной настройке не удаляет существующую колонку', (tester) async {
      bigScreen(tester);
      final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Кухня')];
      final env = await signedIn(server: server, pairing: _WifiFails());
      await env.state.start();
      await tester.pumpWidget(host(env, env.state.devices.single));
      await settle(tester);
      await toWifi(tester);
      await tester.tap(find.text(ru.t('pair_connect')));
      await settle(tester, 12);
      expect(find.textContaining(ru.t('pair_failed')), findsOneWidget);
      expect(server.devices.length, 1, reason: 'существующая колонка не должна удаляться');
      expect(server.requests.where((r) => r.startsWith('DELETE /api/v1/devices')), isEmpty);
    });
  });

  test('адрес для платы: схема и путь отбрасываются, TLS определяется', () {
    expect(serverForBoard('http://192.168.0.136:8000'), (server: '192.168.0.136:8000', tls: false));
    expect(serverForBoard('https://api.example.com/'), (server: 'api.example.com', tls: true));
    expect(serverForBoard('wss://x.y:9/ws'), (server: 'x.y:9', tls: true));
    expect(serverForBoard('10.0.0.5:8000'), (server: '10.0.0.5:8000', tls: false));
  });

  group('адрес сервера (скрытое окно)', () {
    Future<void> openDialog(WidgetTester tester, TestEnv env) async {
      await env.state.start();
      await tester.pumpWidget(NoctisApp(state: env.state));
      await settle(tester);
      final logo = find.byType(NoctisLogo).first;
      for (var i = 0; i < 7; i++) {
        await tester.tap(logo);
        await tester.pump(const Duration(milliseconds: 50));
      }
      await settle(tester);
    }

    testWidgets('6 касаний ничего не открывают, 7 открывают окно с полем адреса', (tester) async {
      bigScreen(tester);
      final env = await TestEnv.create();
      await env.state.start();
      await tester.pumpWidget(NoctisApp(state: env.state));
      await settle(tester);
      for (var i = 0; i < 6; i++) {
        await tester.tap(find.byType(NoctisLogo).first);
        await tester.pump(const Duration(milliseconds: 50));
      }
      await settle(tester);
      expect(find.text(ru.t('dev_server')), findsNothing);
      await tester.tap(find.byType(NoctisLogo).first);
      await settle(tester);
      expect(find.text(ru.t('dev_server')), findsOneWidget);
      expect(find.text(ru.t('test_conn')), findsOneWidget);
    });

    testWidgets('«Проверить соединение»: сервер отвечает / не отвечает / неверный адрес', (tester) async {
      bigScreen(tester);
      final server = FakeServer();
      final env = await TestEnv.create(server: server);
      await openDialog(tester, env);

      await tester.tap(find.text(ru.t('test_conn')));
      await settle(tester);
      expect(find.text(ru.t('conn_ok')), findsOneWidget);

      server.offline = true;
      await tester.tap(find.text(ru.t('test_conn')));
      await settle(tester);
      expect(find.text(ru.t('conn_fail')), findsOneWidget);

      await tester.enterText(find.descendant(of: find.byType(AlertDialog), matching: find.byType(TextField)), 'ftp://x');
      await tester.tap(find.text(ru.t('test_conn')));
      await settle(tester);
      expect(find.text(ru.t('conn_bad')), findsOneWidget);
    });

    testWidgets('сохранение меняет адрес, «адрес по умолчанию» возвращает прежний', (tester) async {
      bigScreen(tester);
      final env = await TestEnv.create();
      final before = env.state.api.baseUrl;
      await openDialog(tester, env);

      await tester.enterText(find.descendant(of: find.byType(AlertDialog), matching: find.byType(TextField)), 'http://10.176.189.233:8000');
      await tester.tap(find.text(ru.t('dev_server_save')));
      await settle(tester);
      expect(env.state.api.baseUrl, 'http://10.176.189.233:8000');
      expect(env.state.prefs.getString('server'), 'http://10.176.189.233:8000');

      await env.state.resetServer();
      expect(env.state.api.baseUrl, isNot('http://10.176.189.233:8000'));
      expect(env.state.prefs.getString('server'), isNull);
      expect(before, isNotEmpty);
    });
  });
}

class _WifiFails extends MockPairing {
  _WifiFails() : super(delay: Duration.zero);

  @override
  Future<void> sendWifi(String ssid, String password) async => throw PairingException('wifi failed');
}

class _LinkFails extends MockPairing {
  _LinkFails() : super(delay: Duration.zero);

  @override
  Future<void> sendLink({required String server, required bool tls, String? deviceId, String? token}) async =>
      throw PairingException('link failed');
}
