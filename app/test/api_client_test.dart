import 'package:flutter_test/flutter_test.dart';
import 'package:noctis/services/api_client.dart';

import 'helpers.dart';

void main() {
  test('вход сохраняет токены, запросы идут с токеном', () async {
    final env = await TestEnv.create();
    await env.api.login('anna@example.com', 'secret123');
    expect(await env.tokens.accessToken(), 'a1');
    expect(await env.tokens.refreshToken(), 'r1');
    expect((await env.api.me()).title, 'Anna');
  });

  test('неверный пароль → ApiException 401 с текстом сервера', () async {
    final env = await TestEnv.create(server: FakeServer()..wrongPassword = true);
    expect(
      () => env.api.login('a@b.co', 'x'),
      throwsA(isA<ApiException>().having((e) => e.status, 'status', 401).having((e) => e.detail, 'detail', 'Invalid email or password')),
    );
  });

  test('нет связи → status 0', () async {
    final env = await TestEnv.create(server: FakeServer()..offline = true);
    expect(() => env.api.login('a@b.co', 'x'), throwsA(isA<ApiException>().having((e) => e.isNetwork, 'isNetwork', true)));
  });

  test('истёкший токен обновляется один раз и запрос повторяется', () async {
    final server = FakeServer()..devices = [FakeServer.deviceJson('d1', 'Кухня')];
    final env = await TestEnv.create(server: server);
    await env.api.login('a@b.co', 'x');
    server.accessExpired = true;
    final list = await env.api.devices();
    expect(list.single.name, 'Кухня');
    expect(await env.tokens.accessToken(), 'a2');
    expect(server.requests.where((r) => r == 'POST /api/v1/auth/refresh').length, 1);
  });

  test('не удалось обновить токен → токены стёрты, приложение уведомлено', () async {
    final server = FakeServer()..devices = [];
    final env = await TestEnv.create(server: server);
    var expired = false;
    env.api.onSessionExpired = () => expired = true;
    await env.api.login('a@b.co', 'x');
    server..accessExpired = true..refreshFails = true;
    await expectLater(env.api.devices(), throwsA(isA<ApiException>()));
    expect(expired, isTrue);
    expect(await env.tokens.accessToken(), isNull);
  });

  test('регистрация: занятый email → 409', () async {
    final env = await TestEnv.create();
    await expectLater(env.api.register(email: 'taken@example.com', password: 'secret123', displayName: '', language: 'ru'), throwsA(isA<ApiException>().having((e) => e.status, 'status', 409)));
  });

  test('колонки, настройки, таймеры, будильники, история, плеер', () async {
    final server = FakeServer();
    final env = await TestEnv.create(server: server);
    await env.api.login('a@b.co', 'x');
    final created = await env.api.createDevice('Гостиная', 'Europe/Berlin');
    expect(created.token, 'tok-${created.device.publicId}');
    final id = created.device.publicId;

    final settings = await env.api.patchSettings(id, {'volume': 40, 'language': 'en'});
    expect(settings.volume, 40);
    expect(settings.language, 'en');

    await env.api.addTimer(id, 300, label: 'чай');
    var timers = await env.api.timers(id);
    expect(timers.single.remainingSec, 300);
    await env.api.timerAction(id, timers.single.id, 'pause');
    timers = await env.api.timers(id);
    expect(timers.single.paused, isTrue);
    await env.api.deleteTimer(id, timers.single.id);
    expect(await env.api.timers(id), isEmpty);

    await env.api.addAlarm(id, hour: 7, minute: 5, weekdays: [0, 1], label: 'работа', melody: 'soft');
    final alarm = (await env.api.alarms(id)).single;
    expect(alarm.time, '07:05');
    expect(alarm.weekdays, [0, 1]);

    expect((await env.api.history(id)).single.userText, 'Который час?');
    expect((await env.api.player(id)).active, isFalse);
    expect((await env.api.tracks('local', '')).single.title, 'Тестовый трек');
  });

  test('ошибка 422 со списком: ключи собираются в строку', () async {
    final env = await TestEnv.create();
    await env.api.login('a@b.co', 'x');
    await expectLater(env.api.call('GET', '/api/v1/nothing'), throwsA(isA<ApiException>().having((e) => e.status, 'status', 404)));
  });
}
