import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../config.dart';
import '../l10n/l10n.dart';
import '../state/app_state.dart';
import '../theme/noctis_theme.dart';
import '../widgets/server_dialog.dart';
import '../widgets/widgets.dart';

/// Профиль: язык, тема, выход, удаление учётной записи, о приложении.
class ProfileTab extends StatefulWidget {
  const ProfileTab({super.key});

  @override
  State<ProfileTab> createState() => _ProfileTabState();
}

class _ProfileTabState extends State<ProfileTab> {
  final _taps = TapCounter();

  Future<void> _delete() async {
    final app = context.read<AppState>();
    final s = app.strings;
    final pw = TextEditingController();
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(s.t('delete_account')),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(s.t('delete_confirm')),
          const SizedBox(height: 12),
          TextField(controller: pw, obscureText: true, decoration: InputDecoration(labelText: s.t('password'))),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: Text(s.t('cancel'))),
          TextButton(onPressed: () => Navigator.pop(c, true), child: Text(s.t('delete'))),
        ],
      ),
    );
    final password = pw.text;
    pw.dispose();
    if (yes != true || password.isEmpty) return;
    try {
      await app.deleteAccount(password);
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(app.errorText(e))));
    }
  }

  /// Мои данные: выгрузка в буфер обмена и очистка истории — только по нажатию пользователя.
  Future<void> _exportData() async {
    final app = context.read<AppState>();
    final s = app.strings;
    try {
      final json = await app.api.exportMyData();
      await Clipboard.setData(ClipboardData(text: json));
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(s.t('priv_copied'))));
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(app.errorText(e))));
    }
  }

  Future<void> _clearHistory() async {
    final app = context.read<AppState>();
    final s = app.strings;
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        content: Text(s.t('priv_clear_confirm')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: Text(s.t('cancel'))),
          TextButton(onPressed: () => Navigator.pop(c, true), child: Text(s.t('priv_clear'))),
        ],
      ),
    );
    if (yes != true) return;
    try {
      final n = await app.api.clearAllHistory();
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(s.t('priv_cleared', {'n': '$n'}))));
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(app.errorText(e))));
    }
  }

  /// Скрытое окно проверки: семь касаний по логотипу.
  void _logoTap() {
    if (_taps.tap()) showServerDialog(context);
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final s = app.strings;
    final p = context.nx.p;
    return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 24), children: [
      GlassCard(
        glow: true,
        child: Row(children: [
          Container(
            width: 52,
            height: 52,
            decoration: BoxDecoration(gradient: context.nx.accent, shape: BoxShape.circle),
            alignment: Alignment.center,
            child: Text((app.user?.title ?? '?').characters.first.toUpperCase(), style: TextStyle(color: p.onBrand, fontWeight: FontWeight.w800, fontSize: 22)),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(app.user?.title ?? '', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
              Text(app.user?.email ?? '', style: TextStyle(color: p.textMuted, fontSize: 13)),
            ]),
          ),
        ]),
      ),
      const SizedBox(height: 14),
      GlassCard(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          LabeledField(
            label: s.t('language_label'),
            child: NoctisDropdown<String>(value: app.lang, items: [for (final l in kLanguages) (l, kLanguageNames[l]!)], onChanged: app.setLanguage),
          ),
          LabeledField(
            label: s.t('theme_label'),
            child: SizedBox(
              width: double.infinity,
              child: SegmentedButton<ThemeMode>(
                showSelectedIcon: false,
                segments: [
                  ButtonSegment(value: ThemeMode.dark, icon: const Icon(Icons.dark_mode_outlined), label: Text(s.t('theme_dark'))),
                  ButtonSegment(value: ThemeMode.light, icon: const Icon(Icons.light_mode_outlined), label: Text(s.t('theme_light'))),
                ],
                selected: {app.themeMode},
                onSelectionChanged: (v) => app.setThemeMode(v.first),
              ),
            ),
          ),
        ]),
      ),
      const SizedBox(height: 14),
      GlassCard(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(s.t('tab_privacy'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
          const SizedBox(height: 6),
          Text(s.t('priv_intro'), style: TextStyle(color: p.textMuted, fontSize: 13, height: 1.4)),
          const SizedBox(height: 12),
          Wrap(spacing: 10, runSpacing: 10, children: [
            GhostButton(label: s.t('priv_export_app'), icon: Icons.copy_all_outlined, onPressed: _exportData),
            GhostButton(label: s.t('priv_clear'), icon: Icons.history_toggle_off, danger: true, onPressed: _clearHistory),
          ]),
        ]),
      ),
      const SizedBox(height: 14),
      GlassCard(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          GhostButton(label: s.t('logout'), icon: Icons.logout, onPressed: app.logout),
          const SizedBox(height: 10),
          GhostButton(label: s.t('delete_account'), icon: Icons.delete_forever_outlined, danger: true, onPressed: _delete),
        ]),
      ),
      const SizedBox(height: 24),
      Center(
        child: Column(children: [
          NoctisLogo(size: 40, onTap: _logoTap),
          const SizedBox(height: 6),
          Text('${s.t('version')} $kAppVersion', style: TextStyle(color: p.textFaint, fontSize: 12.5)),
        ]),
      ),
    ]);
  }
}
