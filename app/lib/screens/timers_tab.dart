import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../l10n/l10n.dart';
import '../models/models.dart';
import '../state/app_state.dart';
import '../theme/noctis_theme.dart';
import '../widgets/widgets.dart';

String formatDuration(int seconds) {
  final sec = seconds < 0 ? 0 : seconds;
  final h = sec ~/ 3600, m = (sec % 3600) ~/ 60, s = sec % 60;
  String two(int n) => n.toString().padLeft(2, '0');
  return h > 0 ? '$h:${two(m)}:${two(s)}' : '$m:${two(s)}';
}

/// Таймеры и будильники.
class TimersTab extends StatefulWidget {
  const TimersTab({super.key, required this.device});
  final Device device;

  @override
  State<TimersTab> createState() => _TimersTabState();
}

class _TimersTabState extends State<TimersTab> {
  List<TimerItem>? _timers;
  List<AlarmItem>? _alarms;
  DateTime _loadedAt = DateTime.now();
  Timer? _tick, _poll;
  String? _error;

  final _minutes = TextEditingController(text: '5');
  final _seconds = TextEditingController(text: '0');
  final _timerLabel = TextEditingController();

  String get _id => widget.device.publicId;

  @override
  void initState() {
    super.initState();
    _load();
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => mounted ? setState(() {}) : null);
    _poll = Timer.periodic(const Duration(seconds: 10), (_) => _load(silent: true));
  }

  @override
  void dispose() {
    _tick?.cancel();
    _poll?.cancel();
    _minutes.dispose();
    _seconds.dispose();
    _timerLabel.dispose();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    final app = context.read<AppState>();
    try {
      final res = await Future.wait([app.api.timers(_id), app.api.alarms(_id)]);
      if (!mounted) return;
      setState(() {
        _timers = res[0] as List<TimerItem>;
        _alarms = res[1] as List<AlarmItem>;
        _loadedAt = DateTime.now();
        _error = null;
      });
    } catch (e) {
      if (mounted && !silent) setState(() => _error = app.errorText(e));
    }
  }

  Future<void> _do(Future<void> Function() action) async {
    final app = context.read<AppState>();
    try {
      await action();
      await _load();
    } catch (e) {
      if (mounted) setState(() => _error = app.errorText(e));
    }
  }

  Future<void> _addAlarm() async {
    final app = context.read<AppState>();
    final res = await showModalBottomSheet<_AlarmDraft>(
      context: context,
      isScrollControlled: true,
      backgroundColor: context.nx.p.bg2,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      builder: (_) => _AlarmSheet(strings: app.strings),
    );
    if (res == null) return;
    await _do(() => app.api.addAlarm(_id, hour: res.time.hour, minute: res.time.minute, weekdays: res.weekdays, label: res.label, melody: res.melody));
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final s = app.strings;
    final p = context.nx.p;
    final spent = DateTime.now().difference(_loadedAt).inSeconds;
    final timers = _timers, alarms = _alarms;

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 24), children: [
        if (!widget.device.online) Notice(s.t('offline_note'), kind: NoticeKind.warn),
        if (_error != null) Notice(_error!),
        GlassCard(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(s.t('timers_title'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
            const SizedBox(height: 12),
            Text('${s.t('quick')}:', style: TextStyle(color: p.textMuted, fontSize: 13)),
            const SizedBox(height: 8),
            Wrap(spacing: 8, runSpacing: 8, children: [
              for (final m in const [1, 5, 10, 15, 30]) GhostButton(label: '$m ${s.t('min_short')}', small: true, onPressed: () => _do(() => context.read<AppState>().api.addTimer(_id, m * 60))),
            ]),
            const SizedBox(height: 14),
            Row(children: [
              Expanded(child: TextField(controller: _minutes, keyboardType: TextInputType.number, decoration: InputDecoration(labelText: s.t('minutes')))),
              const SizedBox(width: 10),
              Expanded(child: TextField(controller: _seconds, keyboardType: TextInputType.number, decoration: InputDecoration(labelText: s.t('seconds')))),
            ]),
            const SizedBox(height: 10),
            TextField(controller: _timerLabel, maxLength: 80, decoration: InputDecoration(labelText: s.t('label'), counterText: '')),
            const SizedBox(height: 12),
            GradientButton(
              label: s.t('add_timer'),
              icon: Icons.timer_outlined,
              onPressed: () {
                final total = (int.tryParse(_minutes.text) ?? 0) * 60 + (int.tryParse(_seconds.text) ?? 0);
                if (total < 1) return;
                _do(() => context.read<AppState>().api.addTimer(_id, total, label: _timerLabel.text.trim()));
              },
            ),
            const SizedBox(height: 14),
            if (timers == null)
              Text(s.t('loading'), style: TextStyle(color: p.textMuted))
            else if (timers.isEmpty)
              Center(child: Padding(padding: const EdgeInsets.all(8), child: Text(s.t('no_timers'), style: TextStyle(color: p.textFaint))))
            else
              for (final t in timers)
                _Row(
                  title: formatDuration(t.paused ? t.remainingSec : t.remainingSec - spent),
                  big: true,
                  subtitle: [if (t.label.isNotEmpty) t.label, if (t.paused) s.t('paused')].join(' · '),
                  actions: [
                    IconButton(tooltip: t.paused ? s.t('resume') : s.t('pause'), icon: Icon(t.paused ? Icons.play_arrow : Icons.pause), onPressed: () => _do(() => context.read<AppState>().api.timerAction(_id, t.id, t.paused ? 'resume' : 'pause'))),
                    IconButton(tooltip: s.t('delete'), icon: Icon(Icons.delete_outline, color: p.danger), onPressed: () => _do(() => context.read<AppState>().api.deleteTimer(_id, t.id))),
                  ],
                ),
          ]),
        ),
        const SizedBox(height: 14),
        GlassCard(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(child: Text(s.t('alarms_title'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800))),
              GhostButton(label: s.t('add_alarm'), icon: Icons.add_alarm, small: true, onPressed: _addAlarm),
            ]),
            const SizedBox(height: 12),
            if (alarms == null)
              Text(s.t('loading'), style: TextStyle(color: p.textMuted))
            else if (alarms.isEmpty)
              Center(child: Padding(padding: const EdgeInsets.all(8), child: Text(s.t('no_alarms'), style: TextStyle(color: p.textFaint))))
            else
              for (final a in alarms)
                _Row(
                  title: a.time,
                  big: true,
                  subtitle: [
                    if (a.weekdays.isEmpty) s.t('once') else if (a.weekdays.length == 7) s.t('every_day') else ([...a.weekdays]..sort()).map(s.day).join(', '),
                    if (a.label.isNotEmpty) a.label,
                    s.melody(a.melody),
                  ].join(' · '),
                  actions: [
                    Switch(value: a.enabled, onChanged: (v) => _do(() => context.read<AppState>().api.setAlarmEnabled(_id, a.id, v))),
                    IconButton(tooltip: s.t('delete'), icon: Icon(Icons.delete_outline, color: p.danger), onPressed: () => _do(() => context.read<AppState>().api.deleteAlarm(_id, a.id))),
                  ],
                ),
          ]),
        ),
      ]),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.title, required this.subtitle, required this.actions, this.big = false});
  final String title;
  final String subtitle;
  final List<Widget> actions;
  final bool big;

  @override
  Widget build(BuildContext context) {
    final p = context.nx.p;
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.fromLTRB(14, 8, 6, 8),
      decoration: BoxDecoration(color: p.bg1, borderRadius: BorderRadius.circular(kRadiusSmall), border: Border.all(color: p.glassBorder)),
      child: Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title, style: TextStyle(fontSize: big ? 26 : 16, fontWeight: FontWeight.w800, fontFeatures: const [FontFeature.tabularFigures()])),
            if (subtitle.isNotEmpty) Text(subtitle, style: TextStyle(color: p.textMuted, fontSize: 12.5)),
          ]),
        ),
        ...actions,
      ]),
    );
  }
}

const double kRadiusSmall = 10;

class _AlarmDraft {
  _AlarmDraft(this.time, this.weekdays, this.label, this.melody);
  final TimeOfDay time;
  final List<int> weekdays;
  final String label;
  final String melody;
}

class _AlarmSheet extends StatefulWidget {
  const _AlarmSheet({required this.strings});
  final Strings strings;

  @override
  State<_AlarmSheet> createState() => _AlarmSheetState();
}

class _AlarmSheetState extends State<_AlarmSheet> {
  TimeOfDay _time = const TimeOfDay(hour: 7, minute: 30);
  final Set<int> _days = {};
  String _melody = 'default';
  final _label = TextEditingController();

  @override
  void dispose() {
    _label.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings;
    final p = context.nx.p;
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
          Text(s.t('add_alarm'), style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
          const SizedBox(height: 14),
          GestureDetector(
            onTap: () async {
              final t = await showTimePicker(context: context, initialTime: _time);
              if (t != null) setState(() => _time = t);
            },
            child: Container(
              padding: const EdgeInsets.symmetric(vertical: 14),
              alignment: Alignment.center,
              decoration: BoxDecoration(color: p.bg1, borderRadius: BorderRadius.circular(kRadiusSmall), border: Border.all(color: p.glassBorder)),
              child: Text('${_time.hour.toString().padLeft(2, '0')}:${_time.minute.toString().padLeft(2, '0')}', style: const TextStyle(fontSize: 40, fontWeight: FontWeight.w800)),
            ),
          ),
          const SizedBox(height: 14),
          Text(s.t('weekdays'), style: TextStyle(color: p.textMuted, fontWeight: FontWeight.w600, fontSize: 13)),
          const SizedBox(height: 6),
          Wrap(spacing: 6, children: [
            for (var i = 0; i < 7; i++)
              FilterChip(
                label: Text(s.day(i)),
                selected: _days.contains(i),
                onSelected: (v) => setState(() => v ? _days.add(i) : _days.remove(i)),
              ),
          ]),
          const SizedBox(height: 10),
          LabeledField(label: s.t('melody'), child: NoctisDropdown<String>(value: _melody, items: [for (final m in const ['default', 'soft', 'rooster', 'beeps']) (m, s.melody(m))], onChanged: (v) => setState(() => _melody = v))),
          TextField(controller: _label, maxLength: 80, decoration: InputDecoration(labelText: s.t('label'), counterText: '')),
          const SizedBox(height: 14),
          GradientButton(label: s.t('add_alarm'), onPressed: () => Navigator.pop(context, _AlarmDraft(_time, _days.toList()..sort(), _label.text.trim(), _melody))),
        ]),
      ),
    );
  }
}
