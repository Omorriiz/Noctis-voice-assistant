// Создано tools/gen_tokens.py из site/assets/tokens.json — не правьте вручную.
import 'dart:ui';

class NoctisPalette {
  const NoctisPalette({
    required this.bg0,
    required this.bg1,
    required this.bg2,
    required this.violet700,
    required this.violet600,
    required this.violet500,
    required this.violet300,
    required this.blue600,
    required this.blue500,
    required this.blue400,
    required this.text,
    required this.textMuted,
    required this.textFaint,
    required this.onBrand,
    required this.glassBg,
    required this.glassBorder,
    required this.glassBorderHover,
    required this.success,
    required this.warning,
    required this.danger,
  });

  final Color bg0;
  final Color bg1;
  final Color bg2;
  final Color violet700;
  final Color violet600;
  final Color violet500;
  final Color violet300;
  final Color blue600;
  final Color blue500;
  final Color blue400;
  final Color text;
  final Color textMuted;
  final Color textFaint;
  final Color onBrand;
  final Color glassBg;
  final Color glassBorder;
  final Color glassBorderHover;
  final Color success;
  final Color warning;
  final Color danger;
}

const NoctisPalette kPaletteDark = NoctisPalette(
  bg0: Color(0xFF05030A),
  bg1: Color(0xFF0A0A12),
  bg2: Color(0xFF11101C),
  violet700: Color(0xFF7C3AED),
  violet600: Color(0xFF9333EA),
  violet500: Color(0xFFA855F7),
  violet300: Color(0xFFC084FC),
  blue600: Color(0xFF2563EB),
  blue500: Color(0xFF3B82F6),
  blue400: Color(0xFF60A5FA),
  text: Color(0xFFEDEAF7),
  textMuted: Color(0xFFA19DB5),
  textFaint: Color(0xFF8D89A6),
  onBrand: Color(0xFFFFFFFF),
  glassBg: Color(0x73141024),
  glassBorder: Color(0x24C084FC),
  glassBorderHover: Color(0x80C084FC),
  success: Color(0xFF4ADE80),
  warning: Color(0xFFFBBF24),
  danger: Color(0xFFF87171),
);

const NoctisPalette kPaletteLight = NoctisPalette(
  bg0: Color(0xFFF6F3FD),
  bg1: Color(0xFFEDE7FA),
  bg2: Color(0xFFFFFFFF),
  violet700: Color(0xFF6D28D9),
  violet600: Color(0xFF7E22CE),
  violet500: Color(0xFF7E22CE),
  violet300: Color(0xFF6D28D9),
  blue600: Color(0xFF1D4ED8),
  blue500: Color(0xFF2563EB),
  blue400: Color(0xFF2563EB),
  text: Color(0xFF1A1030),
  textMuted: Color(0xFF5E5578),
  textFaint: Color(0xFF6B6384),
  onBrand: Color(0xFFFFFFFF),
  glassBg: Color(0xB8FFFFFF),
  glassBorder: Color(0x336D28D9),
  glassBorderHover: Color(0x8C6D28D9),
  success: Color(0xFF15803D),
  warning: Color(0xFFB45309),
  danger: Color(0xFFB91C1C),
);

const double kRadiusSm = 10.0;
const double kRadius = 18.0;
const double kRadiusLg = 28.0;
