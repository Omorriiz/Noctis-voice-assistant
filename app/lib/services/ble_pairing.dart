import 'dart:async';
import 'dart:convert';

import 'package:device_info_plus/device_info_plus.dart';
import 'package:esp_provisioning_ble/esp_provisioning_ble.dart';
// ignore: implementation_imports
import 'package:esp_provisioning_ble/src/protos/generated/session.pb.dart' show SessionData;
import 'package:flutter/foundation.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'package:permission_handler/permission_handler.dart';

import 'pairing.dart';

/// Транспорт BLE для протокола подготовки ESP-IDF (flutter_blue_plus).
///
/// Имена endpoint'ов отображаются на короткие UUID характеристик. Собственный endpoint колонки `noctis-config`
/// создаётся первым среди пользовательских, поэтому получает UUID ff54 — пакет esp_provisioning_ble называет его `custom-data`.
class BleTransport implements ProvTransport {
  BleTransport(this.device);

  final BluetoothDevice device;
  static const customEndpoint = 'noctis-config';
  static const serviceUuid = '021a9004-0382-4aea-bff4-6b3f1c5adfb4';
  static const endpoints = {'prov-scan': 'ff50', 'prov-session': 'ff51', 'prov-config': 'ff52', 'proto-ver': 'ff53', 'custom-data': 'ff54'};

  final Map<String, BluetoothCharacteristic> _chars = {};

  String _uuid(String shortHex) => '${serviceUuid.substring(0, 4)}${shortHex.padLeft(4, '0')}${serviceUuid.substring(8)}';

  @override
  Future<bool> connect() async {
    await disconnect();
    try {
      await device.connect(autoConnect: false, license: License.nonprofit);
    } catch (_) {
      return false;
    }
    try {
      await device.requestMtu(256);
    } catch (_) {
      // MTU по умолчанию тоже подходит
    }
    try {
      // Как у esp_prov: имя endpoint'а берём из дескриптора «Characteristic User Description» (0x2901);
      // порядок UUID ff5x зависит от порядка регистрации endpoint'ов в прошивке, поэтому на него полагаться нельзя.
      final byUuid = {for (final e in endpoints.entries) _uuid(e.value): e.key};
      _chars.clear();
      for (final service in await device.discoverServices()) {
        for (final c in service.characteristics) {
          String? name;
          for (final d in c.descriptors) {
            if (d.uuid.toString().toLowerCase().replaceAll('-', '').endsWith('2901') || d.uuid.toString().toLowerCase() == '2901') {
              try {
                name = utf8.decode(await d.read(), allowMalformed: true).trim().replaceAll('\u0000', '');
              } catch (_) {}
            }
          }
          name ??= byUuid[c.uuid.toString().toLowerCase()];
          if (name == null || name.isEmpty) continue;
          _chars[name == customEndpoint ? 'custom-data' : name] = c;
        }
      }
    } catch (_) {
      return false;
    }
    return device.isConnected;
  }

  @override
  Future<Uint8List> sendReceive(String epName, Uint8List data) async {
    final c = _chars[epName];
    if (c == null) throw StateError('endpoint $epName not found');
    if (data.isNotEmpty) await c.write(data.toList(), withoutResponse: false, allowLongWrite: true);
    return Uint8List.fromList(await c.read());
  }

  @override
  Future<bool> checkConnect() async => device.isConnected;

  @override
  Future<bool> disconnect() async {
    if (device.isConnected) {
      try {
        await device.disconnect();
      } catch (_) {
        return false;
      }
    }
    return true;
  }
}

/// Настоящее сопряжение по Bluetooth: то же, что делает server/tools/pair_device.py.
class BlePairing implements PairingTransport {
  EspProv? _prov;

  @override
  Future<bool> ensurePermissions() async {
    if (defaultTargetPlatform != TargetPlatform.android) return true; // iOS спрашивает сам при первом обращении к Bluetooth
    final sdk = (await DeviceInfoPlugin().androidInfo).version.sdkInt;
    if (sdk >= 31) {
      // Android 12+: достаточно «Устройства поблизости» (геолокация не нужна — в манифесте neverForLocation)
      final st = await [Permission.bluetoothScan, Permission.bluetoothConnect].request();
      return (st[Permission.bluetoothScan]?.isGranted ?? false) && (st[Permission.bluetoothConnect]?.isGranted ?? false);
    }
    // Android 11 и старше: для сканирования BLE система требует геолокацию
    return (await Permission.locationWhenInUse.request()).isGranted;
  }

  @override
  Future<List<FoundSpeaker>> scan({Duration timeout = const Duration(seconds: 8)}) async {
    final found = <String, FoundSpeaker>{};
    final sub = FlutterBluePlus.scanResults.listen((results) {
      for (final r in results) {
        final name = r.advertisementData.advName.isNotEmpty ? r.advertisementData.advName : r.device.platformName;
        if (name.toUpperCase().startsWith('NOCTIS_')) {
          found[r.device.remoteId.str] = FoundSpeaker(id: r.device.remoteId.str, name: name, rssi: r.rssi);
        }
      }
    });
    try {
      await FlutterBluePlus.startScan(timeout: timeout);
      await FlutterBluePlus.isScanning.where((v) => !v).first;
    } finally {
      await sub.cancel();
    }
    final list = found.values.toList()..sort((a, b) => b.rssi.compareTo(a.rssi));
    return list;
  }

  @override
  Future<void> connect(FoundSpeaker speaker, String code) async {
    final transport = BleTransport(BluetoothDevice.fromId(speaker.id));
    if (!await transport.connect()) throw PairingException('connect failed');
    final prov = EspProv(transport: transport, security: Security1(pop: code));
    // Своё рукопожатие вместо prov.establishSession(): пакет любую ошибку при живом соединении выдаёт за «неверный ключ»
    try {
      var response = SessionData();
      while (true) {
        final request = await prov.security.securitySession(response);
        if (request == null) break;
        final raw = await transport.sendReceive('prov-session', request.writeToBuffer());
        if (raw.isEmpty) throw StateError('empty response from speaker');
        response = SessionData.fromBuffer(raw);
      }
      _prov = prov;
      return;
    } catch (e) {
      await transport.disconnect();
      throw PairingException('session: $e', wrongCode: e.toString().contains('Mismatch in device verify'));
    }
  }

  @override
  Future<void> sendLink({required String server, required bool tls, String? deviceId, String? token}) async {
    final prov = _prov ?? (throw PairingException('no session'));
    final payload = jsonEncode({'server': server, 'tls': tls, 'device_id': ?deviceId, 'token': ?token});
    final reply = utf8.decode(await prov.sendReceiveCustomData(Uint8List.fromList(utf8.encode(payload))), allowMalformed: true);
    if (!reply.contains('"ok"')) throw PairingException('board rejected server link');
  }

  @override
  Future<void> sendWifi(String ssid, String password) async {
    final prov = _prov ?? (throw PairingException('no session'));
    if (!await prov.sendWifiConfig(ssid: ssid, password: password)) throw PairingException('wifi config rejected');
    if (!await prov.applyWifiConfig()) throw PairingException('wifi apply failed');
    // Колонка закрывает Bluetooth, как только вышла в сеть, — обрыв связи здесь означает успех.
    for (var i = 0; i < 20; i++) {
      try {
        final st = await prov.getStatus();
        if (st.state == WifiConnectionState.Connected) return;
        if (st.state == WifiConnectionState.ConnectionFailed) throw PairingException('wifi connection failed');
      } on PairingException {
        rethrow;
      } catch (_) {
        return;
      }
      await Future<void>.delayed(const Duration(seconds: 1));
    }
  }

  @override
  Future<void> close() async {
    try {
      await _prov?.dispose();
    } catch (_) {
      // уже закрыто
    }
    _prov = null;
  }
}
