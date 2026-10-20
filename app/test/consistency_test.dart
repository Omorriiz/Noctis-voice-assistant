import 'dart:convert';
import 'dart:io';
import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:noctis/l10n/strings.g.dart';
import 'package:noctis/theme/tokens.g.dart';

Color _hex(String v) => Color(int.parse('FF${v.substring(1)}', radix: 16));

Map<String, Color> _fields(NoctisPalette p) => {
      'bg-0': p.bg0, 'bg-1': p.bg1, 'bg-2': p.bg2,
      'violet-700': p.violet700, 'violet-600': p.violet600, 'violet-500': p.violet500, 'violet-300': p.violet300,
      'blue-600': p.blue600, 'blue-500': p.blue500, 'blue-400': p.blue400,
      'text': p.text, 'text-muted': p.textMuted, 'text-faint': p.textFaint, 'on-brand': p.onBrand,
      'success': p.success, 'warning': p.warning, 'danger': p.danger,
    };

void main() {
  group('палитра совпадает с сайтом (site/assets/tokens.json)', () {
    final tokens = jsonDecode(File('../site/assets/tokens.json').readAsStringSync()) as Map<String, dynamic>;
    for (final theme in ['dark', 'light']) {
      test('тема $theme', () {
        final site = tokens[theme] as Map<String, dynamic>;
        final app = _fields(theme == 'dark' ? kPaletteDark : kPaletteLight);
        for (final e in app.entries) {
          expect(e.value, _hex(site[e.key] as String), reason: '${e.key} в теме $theme');
        }
      });
    }

    test('скругления', () {
      final r = tokens['radius'] as Map<String, dynamic>;
      expect(kRadiusSm, double.parse((r['sm'] as String).replaceAll('px', '')));
      expect(kRadius, double.parse((r['md'] as String).replaceAll('px', '')));
      expect(kRadiusLg, double.parse((r['lg'] as String).replaceAll('px', '')));
    });
  });

  group('строки', () {
    test('пять языков, русский первым', () {
      expect(kLanguages, ['ru', 'en', 'es', 'de', 'fr']);
    });

    test('во всех языках одинаковый набор ключей', () {
      final ru = kStrings['ru']!.keys.toSet();
      for (final l in kLanguages) {
        final keys = kStrings[l]!.keys.toSet();
        expect(keys.difference(ru), isEmpty, reason: 'лишние ключи в $l');
        expect(ru.difference(keys), isEmpty, reason: 'нет ключей в $l');
      }
    });

    test('нет пустых строк и русских букв в других языках', () {
      for (final l in kLanguages) {
        kStrings[l]!.forEach((k, v) {
          expect(v.trim(), isNotEmpty, reason: '$l/$k пусто');
          if (l != 'ru') expect(RegExp('[а-яА-Я]').hasMatch(v), isFalse, reason: '$l/$k содержит русский текст');
        });
      }
    });

    test('каждый ключ, используемый в коде, существует', () {
      final used = <String>{};
      final re = RegExp(r"""\.t\(\s*'([a-z_0-9]+)'""");
      for (final f in Directory('lib').listSync(recursive: true).whereType<File>().where((f) => f.path.endsWith('.dart') && !f.path.endsWith('.g.dart'))) {
        used.addAll(re.allMatches(f.readAsStringSync()).map((m) => m.group(1)!));
      }
      expect(used, isNotEmpty);
      expect(used.difference(kStrings['ru']!.keys.toSet()), isEmpty);
    });
  });

  test('в приложении нет Firebase и push-уведомлений', () {
    final pubspec = File('pubspec.yaml').readAsStringSync().toLowerCase();
    for (final bad in ['firebase', 'cloud_firestore', 'firebase_messaging', 'mqtt']) {
      expect(pubspec.contains(bad), isFalse, reason: bad);
    }
    expect(File('firebase.json').existsSync(), isFalse);
    expect(File('android/app/google-services.json').existsSync(), isFalse);
  });
}
