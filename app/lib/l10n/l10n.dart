import 'package:flutter/widgets.dart';
import 'package:provider/provider.dart';

import '../state/app_state.dart';
import 'strings.g.dart';

export 'strings.g.dart' show kLanguages, kLanguageNames;

/// Строки на выбранном языке. Недостающий ключ берётся из английского, затем показывается как есть.
class Strings {
  const Strings(this.lang);
  final String lang;

  String t(String key, [Map<String, String>? vars]) {
    var s = kStrings[lang]?[key] ?? kStrings['en']![key] ?? key;
    if (vars != null) {
      vars.forEach((k, v) => s = s.replaceAll('{$k}', v));
    }
    return s;
  }

  String day(int i) => t('day_$i');
  String melody(String id) => kStrings[lang]!['melody_$id'] ?? id;
}

/// Язык по умолчанию: системный, если он поддерживается, иначе русский (основной язык проекта).
String pickLanguage(String? systemCode) {
  final code = (systemCode ?? '').toLowerCase();
  return kLanguages.contains(code) ? code : 'ru';
}

extension StringsContext on BuildContext {
  Strings get s => watch<AppState>().strings;
  Strings get sRead => read<AppState>().strings;
}
