import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import '../theme/noctis_theme.dart';
import '../theme/tokens.g.dart';

/// Космический фон как на сайте: градиент, светящиеся пятна и звёзды (звёзды только в тёмной теме).
class CosmosBackground extends StatelessWidget {
  const CosmosBackground({super.key, required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final nx = context.nx;
    final p = nx.p;
    return Stack(fit: StackFit.expand, children: [
      DecoratedBox(
        decoration: BoxDecoration(
          gradient: LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [p.bg0, p.bg1, p.bg0]),
        ),
      ),
      Positioned.fill(child: CustomPaint(painter: _GlowPainter(dark: nx.dark, p: p))),
      if (nx.dark) const Positioned.fill(child: CustomPaint(painter: _StarsPainter())),
      child,
    ]);
  }
}

class _GlowPainter extends CustomPainter {
  _GlowPainter({required this.dark, required this.p});
  final bool dark;
  final NoctisPalette p;

  @override
  void paint(Canvas canvas, Size size) {
    void blob(Offset c, double r, Color color) {
      final rect = Rect.fromCircle(center: c, radius: r);
      canvas.drawCircle(c, r, Paint()..shader = ui.Gradient.radial(c, r, [color, color.withValues(alpha: 0)]));
      rect.hashCode;
    }

    final k = dark ? 1.0 : 0.5;
    blob(Offset(size.width * 0.5, -size.height * 0.02), size.width * 1.0, p.violet700.withValues(alpha: 0.30 * k));
    blob(Offset(size.width * 1.05, size.height * 0.42), size.width * 0.7, p.blue600.withValues(alpha: 0.16 * k));
    blob(Offset(-size.width * 0.05, size.height * 0.78), size.width * 0.7, p.violet500.withValues(alpha: 0.16 * k));
  }

  @override
  bool shouldRepaint(covariant _GlowPainter old) => old.dark != dark;
}

class _StarsPainter extends CustomPainter {
  const _StarsPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final rnd = math.Random(7);
    final paint = Paint();
    for (var i = 0; i < 90; i++) {
      final x = rnd.nextDouble() * size.width;
      final y = rnd.nextDouble() * size.height;
      final r = 0.5 + rnd.nextDouble() * 1.1;
      final violet = rnd.nextInt(4) == 0;
      paint.color = (violet ? const Color(0xFFC084FC) : const Color(0xFFFFFFFF)).withValues(alpha: 0.25 + rnd.nextDouble() * 0.55);
      canvas.drawCircle(Offset(x, y), r, paint);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter old) => false;
}

/// Стеклянная карточка (как .glass на сайте).
class GlassCard extends StatelessWidget {
  const GlassCard({super.key, required this.child, this.padding = const EdgeInsets.all(18), this.onTap, this.glow = false, this.margin});
  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;
  final bool glow;
  final EdgeInsetsGeometry? margin;

  @override
  Widget build(BuildContext context) {
    final nx = context.nx;
    final radius = BorderRadius.circular(kRadius);
    return Container(
      margin: margin,
      decoration: BoxDecoration(borderRadius: radius, boxShadow: glow ? nx.glowMd : null),
      child: ClipRRect(
        borderRadius: radius,
        child: BackdropFilter(
          filter: ui.ImageFilter.blur(sigmaX: 14, sigmaY: 14),
          child: Material(
            color: nx.p.glassBg,
            child: InkWell(
              onTap: onTap,
              child: Container(
                padding: padding,
                decoration: BoxDecoration(borderRadius: radius, border: Border.all(color: nx.p.glassBorder)),
                child: child,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Текст с неоновым градиентом (как .text-glow на сайте).
class GradientText extends StatelessWidget {
  const GradientText(this.text, {super.key, this.style, this.textAlign});
  final String text;
  final TextStyle? style;
  final TextAlign? textAlign;

  @override
  Widget build(BuildContext context) {
    final g = context.nx.headline;
    return ShaderMask(
      blendMode: BlendMode.srcIn,
      shaderCallback: (r) => g.createShader(Rect.fromLTWH(0, 0, r.width, r.height)),
      child: Text(text, style: style, textAlign: textAlign),
    );
  }
}

/// Заголовок: обычная часть + последнее слово с градиентом (как на сайте).
class GlowTitle extends StatelessWidget {
  const GlowTitle(this.text, {super.key, this.size = 30, this.align = TextAlign.start});
  final String text;
  final double size;
  final TextAlign align;

  @override
  Widget build(BuildContext context) {
    final i = text.lastIndexOf(' ');
    final style = TextStyle(fontSize: size, fontWeight: FontWeight.w800, height: 1.15, letterSpacing: -0.5, color: context.nx.p.text);
    final g = context.nx.headline;
    if (i < 0) return GradientText(text, style: style, textAlign: align);
    return Text.rich(
      TextSpan(children: [
        TextSpan(text: '${text.substring(0, i)} '),
        WidgetSpan(
          alignment: PlaceholderAlignment.baseline,
          baseline: TextBaseline.alphabetic,
          child: ShaderMask(
            blendMode: BlendMode.srcIn,
            shaderCallback: (r) => g.createShader(Rect.fromLTWH(0, 0, r.width, r.height)),
            child: Text(text.substring(i + 1), style: style),
          ),
        ),
      ]),
      style: style,
      textAlign: align,
    );
  }
}

/// Кнопка-градиент (как .btn--primary).
class GradientButton extends StatelessWidget {
  const GradientButton({super.key, required this.label, required this.onPressed, this.loading = false, this.icon, this.expand = true});
  final String label;
  final VoidCallback? onPressed;
  final bool loading;
  final IconData? icon;
  final bool expand;

  @override
  Widget build(BuildContext context) {
    final nx = context.nx;
    final enabled = onPressed != null && !loading;
    return Opacity(
      opacity: enabled || loading ? 1 : 0.5,
      child: DecoratedBox(
        decoration: BoxDecoration(gradient: nx.accent, borderRadius: BorderRadius.circular(999), boxShadow: enabled ? nx.glowSm : null),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: BorderRadius.circular(999),
            onTap: enabled ? onPressed : null,
            child: Container(
              constraints: const BoxConstraints(minHeight: 48),
              padding: const EdgeInsets.symmetric(horizontal: 26, vertical: 12),
              alignment: Alignment.center,
              child: Row(mainAxisSize: expand ? MainAxisSize.max : MainAxisSize.min, mainAxisAlignment: MainAxisAlignment.center, children: [
                if (loading)
                  SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: nx.p.onBrand))
                else ...[
                  if (icon != null) ...[Icon(icon, size: 18, color: nx.p.onBrand), const SizedBox(width: 8)],
                  Flexible(child: Text(label, textAlign: TextAlign.center, style: TextStyle(color: nx.p.onBrand, fontWeight: FontWeight.w700, fontSize: 15))),
                ],
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

/// Кнопка-контур (как .btn--ghost).
class GhostButton extends StatelessWidget {
  const GhostButton({super.key, required this.label, required this.onPressed, this.icon, this.danger = false, this.small = false});
  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final bool danger;
  final bool small;

  @override
  Widget build(BuildContext context) {
    final p = context.nx.p;
    final color = danger ? p.danger : p.text;
    return Opacity(
      opacity: onPressed == null ? 0.5 : 1,
      child: Material(
        color: p.glassBg,
        shape: StadiumBorder(side: BorderSide(color: danger ? p.danger : p.violet300.withValues(alpha: 0.4))),
        child: InkWell(
          customBorder: const StadiumBorder(),
          onTap: onPressed,
          child: Container(
            constraints: BoxConstraints(minHeight: small ? 38 : 46),
            padding: EdgeInsets.symmetric(horizontal: small ? 14 : 22, vertical: 8),
            child: Row(mainAxisSize: MainAxisSize.min, mainAxisAlignment: MainAxisAlignment.center, children: [
              if (icon != null) ...[Icon(icon, size: 16, color: color), const SizedBox(width: 6)],
              Flexible(child: Text(label, style: TextStyle(color: color, fontWeight: FontWeight.w700, fontSize: small ? 13 : 14), textAlign: TextAlign.center)),
            ]),
          ),
        ),
      ),
    );
  }
}

/// Подпись раздела капсом (как .label).
class SectionLabel extends StatelessWidget {
  const SectionLabel(this.text, {super.key, this.dot = false});
  final String text;
  final bool dot;

  @override
  Widget build(BuildContext context) {
    final p = context.nx.p;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      if (dot) ...[Container(width: 8, height: 8, decoration: BoxDecoration(color: p.violet300, shape: BoxShape.circle, boxShadow: context.nx.glowSm)), const SizedBox(width: 10)],
      Text(text.toUpperCase(), style: TextStyle(color: p.violet300, fontSize: 11.5, fontWeight: FontWeight.w700, letterSpacing: 2.4)),
    ]);
  }
}

/// Логотип: иконка приложения и название.
class NoctisLogo extends StatelessWidget {
  const NoctisLogo({super.key, this.size = 34, this.onTap, this.showName = true});
  final double size;
  final VoidCallback? onTap;
  final bool showName;

  @override
  Widget build(BuildContext context) {
    final nx = context.nx;
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Container(
          decoration: BoxDecoration(borderRadius: BorderRadius.circular(size * 0.27), boxShadow: nx.dark ? nx.glowSm : null),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(size * 0.27),
            child: Image.asset('assets/icon/noctis_logo.png', width: size, height: size, fit: BoxFit.cover),
          ),
        ),
        if (showName) ...[
          SizedBox(width: size * 0.32),
          Text('NOCTIS', style: TextStyle(fontSize: size * 0.58, fontWeight: FontWeight.w900, letterSpacing: size * 0.2, color: nx.p.text)),
        ],
      ]),
    );
  }
}

enum NoticeKind { error, warn, ok }

/// Сообщение (ошибка, предупреждение, успех).
class Notice extends StatelessWidget {
  const Notice(this.text, {super.key, this.kind = NoticeKind.error});
  final String text;
  final NoticeKind kind;

  @override
  Widget build(BuildContext context) {
    final p = context.nx.p;
    final color = switch (kind) { NoticeKind.error => p.danger, NoticeKind.warn => p.warning, NoticeKind.ok => p.success };
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(borderRadius: BorderRadius.circular(kRadiusSm), border: Border.all(color: color), color: color.withValues(alpha: 0.08)),
      child: Text(text, style: TextStyle(color: color, fontWeight: FontWeight.w600, height: 1.35)),
    );
  }
}

class OnlineBadge extends StatelessWidget {
  const OnlineBadge({super.key, required this.online, required this.onlineText, required this.offlineText});
  final bool online;
  final String onlineText;
  final String offlineText;

  @override
  Widget build(BuildContext context) {
    final p = context.nx.p;
    final color = online ? p.success : p.textFaint;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Container(width: 10, height: 10, decoration: BoxDecoration(color: color, shape: BoxShape.circle, boxShadow: online ? [BoxShadow(color: color, blurRadius: 8)] : null)),
      const SizedBox(width: 8),
      Text(online ? onlineText : offlineText, style: TextStyle(color: online ? p.success : p.textMuted, fontWeight: FontWeight.w700, fontSize: 13)),
    ]);
  }
}

/// Подпись поля над элементом управления.
class LabeledField extends StatelessWidget {
  const LabeledField({super.key, required this.label, required this.child});
  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(padding: const EdgeInsets.only(bottom: 6, left: 2), child: Text(label, style: TextStyle(color: context.nx.p.textMuted, fontWeight: FontWeight.w600, fontSize: 13))),
        child,
      ]),
    );
  }
}

/// Выпадающий список в стиле приложения.
class NoctisDropdown<T> extends StatelessWidget {
  const NoctisDropdown({super.key, required this.value, required this.items, required this.onChanged});
  final T value;
  final List<(T, String)> items;
  final ValueChanged<T> onChanged;

  @override
  Widget build(BuildContext context) {
    return DropdownButtonFormField<T>(
      initialValue: items.any((e) => e.$1 == value) ? value : null,
      isExpanded: true,
      dropdownColor: context.nx.p.bg2,
      items: [for (final e in items) DropdownMenuItem<T>(value: e.$1, child: Text(e.$2, overflow: TextOverflow.ellipsis))],
      onChanged: (v) {
        if (v != null) onChanged(v);
      },
    );
  }
}
