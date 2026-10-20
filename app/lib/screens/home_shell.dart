import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../l10n/l10n.dart';
import '../state/app_state.dart';
import '../theme/noctis_theme.dart';
import '../widgets/widgets.dart';
import 'history_tab.dart';
import 'music_tab.dart';
import 'pairing_screen.dart';
import 'profile_tab.dart';
import 'speaker_tab.dart';
import 'timers_tab.dart';

/// Главный экран: выбор колонки сверху, нижняя панель с разделами.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    // состояние «онлайн» обновляется раз в 8 секунд, как в веб-кабинете
    _poll = Timer.periodic(const Duration(seconds: 8), (_) {
      if (mounted) context.read<AppState>().refreshDevices().catchError((_) {});
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  void _addSpeaker() {
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => const PairingScreen()));
  }

  @override
  Widget build(BuildContext context) {
    final s = context.s;
    final app = context.watch<AppState>();
    final d = app.device;
    final profileIndex = 4;
    final needsDevice = _index != profileIndex;

    Widget body;
    if (needsDevice && d == null) {
      body = _NoDevices(onAdd: _addSpeaker);
    } else {
      body = switch (_index) {
        0 => SpeakerTab(key: ValueKey('speaker-${d!.publicId}'), device: d),
        1 => TimersTab(key: ValueKey('timers-${d!.publicId}'), device: d),
        2 => MusicTab(key: ValueKey('music-${d!.publicId}'), device: d),
        3 => HistoryTab(key: ValueKey('history-${d!.publicId}'), device: d),
        _ => const ProfileTab(),
      };
    }

    return Scaffold(
      appBar: AppBar(
        titleSpacing: 16,
        title: const NoctisLogo(size: 30),
        actions: [
          if (app.devices.length > 1)
            PopupMenuButton<String>(
              tooltip: s.t('choose_speaker'),
              color: context.nx.p.bg2,
              onSelected: app.selectDevice,
              itemBuilder: (_) => [
                for (final x in app.devices)
                  PopupMenuItem(value: x.publicId, child: Row(children: [Icon(Icons.circle, size: 10, color: x.online ? context.nx.p.success : context.nx.p.textFaint), const SizedBox(width: 10), Text(x.name)])),
              ],
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 8),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Text(d?.name ?? '', style: const TextStyle(fontWeight: FontWeight.w700)),
                  const Icon(Icons.arrow_drop_down),
                ]),
              ),
            ),
          IconButton(tooltip: s.t('add_speaker'), onPressed: _addSpeaker, icon: const Icon(Icons.add_circle_outline)),
          const SizedBox(width: 4),
        ],
      ),
      body: SafeArea(top: false, child: body),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: [
          NavigationDestination(icon: const Icon(Icons.speaker_outlined), selectedIcon: const Icon(Icons.speaker), label: s.t('tab_speaker')),
          NavigationDestination(icon: const Icon(Icons.alarm_outlined), selectedIcon: const Icon(Icons.alarm), label: s.t('tab_timers_short')),
          NavigationDestination(icon: const Icon(Icons.music_note_outlined), selectedIcon: const Icon(Icons.music_note), label: s.t('tab_music')),
          NavigationDestination(icon: const Icon(Icons.history), selectedIcon: const Icon(Icons.history_toggle_off), label: s.t('tab_history')),
          NavigationDestination(icon: const Icon(Icons.person_outline), selectedIcon: const Icon(Icons.person), label: s.t('tab_profile')),
        ],
      ),
    );
  }
}

class _NoDevices extends StatelessWidget {
  const _NoDevices({required this.onAdd});
  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) {
    final s = context.s;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: GlassCard(
          glow: true,
          padding: const EdgeInsets.all(26),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const NoctisLogo(size: 64, showName: false),
            const SizedBox(height: 18),
            Text(s.t('no_devices'), style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800), textAlign: TextAlign.center),
            const SizedBox(height: 8),
            Text(s.t('pair_intro'), style: TextStyle(color: context.nx.p.textMuted, height: 1.4), textAlign: TextAlign.center),
            const SizedBox(height: 20),
            GradientButton(label: s.t('add_speaker'), icon: Icons.bluetooth, onPressed: onAdd),
          ]),
        ),
      ),
    );
  }
}
