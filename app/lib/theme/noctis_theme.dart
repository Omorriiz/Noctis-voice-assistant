import 'package:flutter/material.dart';

import 'tokens.g.dart';

/// Оформление Noctis: те же цвета, что на сайте (site/assets/tokens.json → tokens.g.dart).
/// Тёмная тема — основная, светлая — та же палитра на светлом фоне.
@immutable
class NoctisTheme extends ThemeExtension<NoctisTheme> {
  const NoctisTheme({required this.p, required this.dark});

  final NoctisPalette p;
  final bool dark;

  /// Кнопки и акценты: фиолетовый → пурпурный → синий (как --grad-accent на сайте).
  LinearGradient get accent => LinearGradient(colors: [p.violet700, p.violet600, p.blue600], begin: Alignment.topLeft, end: Alignment.bottomRight);

  /// Неоновый градиент в заголовках (как --grad-text).
  LinearGradient get headline => LinearGradient(colors: [p.violet300, p.violet500, p.blue400]);

  List<BoxShadow> get glowSm => [BoxShadow(color: p.violet500.withValues(alpha: dark ? 0.45 : 0.25), blurRadius: 12)];
  List<BoxShadow> get glowMd => [
        BoxShadow(color: p.violet500.withValues(alpha: dark ? 0.45 : 0.22), blurRadius: 24),
        BoxShadow(color: p.violet700.withValues(alpha: dark ? 0.25 : 0.1), blurRadius: 60),
      ];

  @override
  NoctisTheme copyWith({NoctisPalette? p, bool? dark}) => NoctisTheme(p: p ?? this.p, dark: dark ?? this.dark);

  @override
  NoctisTheme lerp(ThemeExtension<NoctisTheme>? other, double t) => t < 0.5 ? this : (other as NoctisTheme? ?? this);

  static ThemeData build({required bool dark}) {
    final p = dark ? kPaletteDark : kPaletteLight;
    final base = ThemeData(brightness: dark ? Brightness.dark : Brightness.light, useMaterial3: true);
    final scheme = ColorScheme(
      brightness: dark ? Brightness.dark : Brightness.light,
      primary: p.violet700,
      onPrimary: p.onBrand,
      secondary: p.violet300,
      onSecondary: p.bg0,
      error: p.danger,
      onError: p.bg0,
      surface: p.bg2,
      onSurface: p.text,
      surfaceContainerHighest: p.bg1,
      outline: p.glassBorder,
    );
    return base.copyWith(
      colorScheme: scheme,
      scaffoldBackgroundColor: Colors.transparent,
      canvasColor: p.bg2,
      dividerColor: p.glassBorder,
      textTheme: base.textTheme.apply(bodyColor: p.text, displayColor: p.text),
      iconTheme: IconThemeData(color: p.textMuted),
      appBarTheme: AppBarTheme(backgroundColor: Colors.transparent, elevation: 0, scrolledUnderElevation: 0, foregroundColor: p.text),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: p.bg0.withValues(alpha: dark ? 0.85 : 0.9),
        indicatorColor: p.violet700.withValues(alpha: 0.28),
        labelTextStyle: WidgetStatePropertyAll(TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: p.textMuted)),
        iconTheme: WidgetStateProperty.resolveWith((s) => IconThemeData(color: s.contains(WidgetState.selected) ? p.violet300 : p.textMuted)),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: p.bg1,
        labelStyle: TextStyle(color: p.textMuted),
        hintStyle: TextStyle(color: p.textFaint),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(kRadiusSm), borderSide: BorderSide(color: p.glassBorder)),
        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(kRadiusSm), borderSide: BorderSide(color: p.glassBorder)),
        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(kRadiusSm), borderSide: BorderSide(color: p.violet300, width: 1.5)),
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? p.onBrand : p.textMuted),
        trackColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? p.violet600 : p.bg1),
      ),
      sliderTheme: SliderThemeData(activeTrackColor: p.violet600, thumbColor: p.violet300, inactiveTrackColor: p.glassBorder),
      checkboxTheme: CheckboxThemeData(fillColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? p.violet600 : Colors.transparent)),
      dialogTheme: DialogThemeData(backgroundColor: p.bg2, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(kRadius), side: BorderSide(color: p.glassBorder))),
      snackBarTheme: SnackBarThemeData(backgroundColor: p.bg2, contentTextStyle: TextStyle(color: p.text), behavior: SnackBarBehavior.floating),
      extensions: [NoctisTheme(p: p, dark: dark)],
    );
  }
}

extension NoctisContext on BuildContext {
  NoctisTheme get nx => Theme.of(this).extension<NoctisTheme>()!;
}
