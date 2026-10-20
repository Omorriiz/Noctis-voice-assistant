/// Настройки сборки. Конечный пользователь адрес сервера не вводит: его задаёт владелец при сборке:
///   flutter build apk --dart-define=NOCTIS_SERVER=https://api.example.com
/// Для разработки есть скрытое меню (семь касаний по логотипу в «О приложении»).
const String kServerDefault = String.fromEnvironment('NOCTIS_SERVER', defaultValue: 'http://localhost:8000');

/// true — Bluetooth заменён заглушкой (веб-версия, тесты, показ без платы): `--dart-define=NOCTIS_MOCK_BLE=true`.
const bool kMockBle = bool.fromEnvironment('NOCTIS_MOCK_BLE', defaultValue: false);

const String kAppVersion = '1.4.0';
