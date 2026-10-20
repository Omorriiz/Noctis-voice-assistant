import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../state/app_state.dart';
import '../theme/noctis_theme.dart';
import '../widgets/widgets.dart';
import 'pairing_screen.dart';
import 'timezones.dart';

/// Колонка: состояние и настройки.
class SpeakerTab extends StatefulWidget {
  const SpeakerTab({super.key, required this.device});
  final Device device;

  @override
  State<SpeakerTab> createState() => _SpeakerTabState();
}

class _SpeakerTabState extends State<SpeakerTab> {
  late final TextEditingController _name;
  late final TextEditingController _homeCity;
  late double _volume;
  late String _language, _voice, _character, _wake, _tz;
  bool _busy = false;
  String? _error;
  String? _ok;
  bool _wifiBusy = false;

  @override
  void initState() {
    super.initState();
    final d = widget.device, st = d.settings;
    _name = TextEditingController(text: d.name);
    _homeCity = TextEditingController(text: st.homeCity);
    _volume = st.volume.toDouble();
    _language = st.language;
    _voice = st.voiceGender;
    _character = st.characterId;
    _wake = st.wakeWord;
    _tz = st.timezone;
  }

  @override
  void dispose() {
    _name.dispose();
    _homeCity.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final app = context.read<AppState>();
    final s = app.strings;
    setState(() {
      _busy = true;
      _error = null;
      _ok = null;
    });
    try {
      final name = _name.text.trim();
      if (name.isNotEmpty && name != widget.device.name) await app.api.renameDevice(widget.device.publicId, name);
      await app.api.patchSettings(widget.device.publicId, {
        'volume': _volume.round(),
        'language': _language,
        'voice_gender': _voice,
        'character_id': _character,
        'wake_word': _wake,
        'timezone': _tz,
        'home_city': _homeCity.text.trim(),
      });
      await app.refreshDevices();
      if (mounted) setState(() => _ok = s.t('saved'));
    } catch (e) {
      if (mounted) setState(() => _error = app.errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _reboot() async {
    final app = context.read<AppState>();
    final s = app.strings;
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        content: Text(s.t('reboot_confirm')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: Text(s.t('cancel'))),
          TextButton(onPressed: () => Navigator.pop(c, true), child: Text(s.t('reboot'))),
        ],
      ),
    );
    if (yes != true) return;
    try {
      await app.api.reboot(widget.device.publicId);
      if (mounted) setState(() => _ok = s.t('rebooted'));
    } catch (e) {
      if (mounted) setState(() => _error = app.errorText(e));
    }
  }

  /// Удаление колонки из учётной записи. Колонка на связи сама сбросит настройки и вернётся в режим подключения.
  Future<void> _delete() async {
    final app = context.read<AppState>();
    final s = app.strings;
    final d = widget.device;
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        content: Text(s.t('dev_delete_confirm', {'name': d.name}) + (d.online ? s.t('dev_delete_online') : s.t('dev_delete_offline'))),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: Text(s.t('cancel'))),
          TextButton(onPressed: () => Navigator.pop(c, true), child: Text(s.t('dev_delete'))),
        ],
      ),
    );
    if (yes != true) return;
    try {
      await app.api.deleteDevice(d.publicId);
      await app.refreshDevices();
    } catch (e) {
      if (mounted) setState(() => _error = app.errorText(e));
    }
  }

  /// Смена Wi‑Fi через сервер (колонка должна быть на связи). Итог опрашиваем до ~100 секунд.
  Future<void> _changeWifi() async {
    final app = context.read<AppState>();
    final s = app.strings;
    final creds = await showDialog<({String ssid, String password})>(context: context, builder: (_) => const _WifiDialog());
    if (creds == null || creds.ssid.isEmpty) return;
    final name = creds.ssid, password = creds.password;
    setState(() {
      _error = null;
      _ok = s.t('wifi_pending');
      _wifiBusy = true;
    });
    try {
      await app.api.changeWifi(widget.device.publicId, name, password);
      for (var i = 0; i < 50 && mounted; i++) {
        await Future<void>.delayed(app.wifiPollInterval);
        WifiStatus st;
        try {
          st = await app.api.wifiStatus(widget.device.publicId);
        } catch (_) {
          continue; // связь могла прерваться на время смены сети
        }
        if (st.state == 'ok') {
          if (mounted) setState(() => _ok = s.t('wifi_ok', {'ssid': name}));
          await app.refreshDevices().catchError((_) {});
          return;
        }
        if (st.state == 'failed') {
          if (mounted) {
            setState(() {
              _ok = null;
              _error = s.t('wifi_failed', {'ssid': name});
            });
          }
          return;
        }
        if (st.state == 'timeout') break;
      }
      if (mounted) {
        setState(() {
          _ok = null;
          _error = s.t('wifi_timeout');
        });
      }
    } catch (e) {
      final status = e is ApiException ? e.status : 0;
      if (mounted) {
        setState(() {
          _ok = null;
          _error = status == 422 ? s.t('wifi_invalid') : (status == 409 && e is ApiException && e.detail.contains('progress')) ? s.t('wifi_busy') : app.errorText(e);
        });
      }
    } finally {
      if (mounted) setState(() => _wifiBusy = false);
    }
  }

  void _reconfigureOverBluetooth() {
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => PairingScreen(reconfigure: widget.device)));
  }

  Future<void> _detectZone() async {
    final tz = await context.read<AppState>().localTimezone();
    if (mounted) setState(() => _tz = tz);
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final s = app.strings;
    final p = context.nx.p;
    final d = widget.device;
    final cat = app.catalog;
    final zones = timezoneChoices(_tz);
    final character = cat?.characters.where((c) => c.id == _character).firstOrNull;

    return RefreshIndicator(
      onRefresh: app.refreshDevices,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 24), children: [
        if (!d.online) Notice(s.t('offline_note'), kind: NoticeKind.warn),
        GlassCard(
          padding: const EdgeInsets.all(18),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(child: Text(d.name, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800))),
              OnlineBadge(online: d.online, onlineText: s.t('online'), offlineText: s.t('offline')),
            ]),
            const SizedBox(height: 4),
            Text('${s.t('firmware')}: ${d.firmwareVersion.isEmpty ? '—' : d.firmwareVersion} · ${s.t('last_seen')}: ${d.lastSeen == null ? s.t('never') : _fmt(d.lastSeen!)}',
                style: TextStyle(color: p.textMuted, fontSize: 12.5)),
            const SizedBox(height: 18),
            if (_error != null) Notice(_error!),
            if (_ok != null) Notice(_ok!, kind: NoticeKind.ok),
            LabeledField(label: s.t('name'), child: TextField(controller: _name, maxLength: 80, decoration: const InputDecoration(counterText: ''))),
            LabeledField(
              label: '${s.t('volume')} — ${_volume.round()} %',
              child: Row(children: [
                const Icon(Icons.volume_mute, size: 20),
                Expanded(child: Slider(value: _volume, min: 0, max: 100, divisions: 20, onChanged: (v) => setState(() => _volume = v))),
                const Icon(Icons.volume_up, size: 20),
              ]),
            ),
            if (cat != null) ...[
              LabeledField(label: s.t('language'), child: NoctisDropdown<String>(value: _language, items: [for (final l in cat.languages) (l.id, l.label)], onChanged: (v) => setState(() => _language = v))),
              LabeledField(
                label: s.t('voice'),
                child: NoctisDropdown<String>(value: _voice, items: [('male', s.t('voice_male')), ('female', s.t('voice_female'))], onChanged: (v) => setState(() => _voice = v)),
              ),
              LabeledField(label: s.t('character'), child: NoctisDropdown<String>(value: _character, items: [for (final c in cat.characters) (c.id, c.label)], onChanged: (v) => setState(() => _character = v))),
              if (character != null && character.description.isNotEmpty) Padding(padding: const EdgeInsets.only(bottom: 14), child: Text(character.description, style: TextStyle(color: p.textMuted, fontSize: 13))),
              LabeledField(
                label: s.t('wake_word'),
                child: NoctisDropdown<String>(
                  value: _wake,
                  items: [
                    for (final w in cat.wakeWords) (w.id, w.ready ? w.label : '${w.label} — ${s.t('no_model')}'),
                    if (!cat.wakeWords.any((w) => w.id == _wake)) (_wake, _wake.replaceFirst('custom:', '')),
                  ],
                  onChanged: (v) => setState(() => _wake = v),
                ),
              ),
            ],
            LabeledField(
              label: s.t('timezone'),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                NoctisDropdown<String>(value: _tz, items: [for (final z in zones) (z, z)], onChanged: (v) => setState(() => _tz = v)),
                const SizedBox(height: 8),
                GhostButton(label: s.t('tz_detect'), icon: Icons.my_location, small: true, onPressed: _detectZone),
              ]),
            ),
            LabeledField(
              label: s.t('home_city'),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                TextField(controller: _homeCity, maxLength: 80, decoration: const InputDecoration(counterText: '')),
                const SizedBox(height: 6),
                Text(s.t('home_city_hint'), style: TextStyle(color: p.textMuted, fontSize: 12.5, height: 1.4)),
              ]),
            ),
            const SizedBox(height: 4),
            GradientButton(label: s.t('save'), loading: _busy, onPressed: _save),
            const SizedBox(height: 12),
            Wrap(alignment: WrapAlignment.center, spacing: 10, runSpacing: 10, children: [
              GhostButton(label: s.t('reboot'), icon: Icons.restart_alt, danger: true, onPressed: d.online ? _reboot : null),
              GhostButton(label: s.t('dev_delete'), icon: Icons.delete_outline, danger: true, onPressed: _delete),
            ]),
          ]),
        ),
        const SizedBox(height: 14),
        GlassCard(
          padding: const EdgeInsets.all(18),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(s.t('wifi_h'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
            const SizedBox(height: 6),
            Text(d.online ? s.t('wifi_note') : s.t('wifi_offline_app'), style: TextStyle(color: p.textMuted, fontSize: 13, height: 1.4)),
            const SizedBox(height: 12),
            Wrap(spacing: 10, runSpacing: 10, children: [
              if (d.online) GradientButton(label: s.t('wifi_apply'), icon: Icons.wifi, loading: _wifiBusy, onPressed: _wifiBusy ? null : _changeWifi),
              GhostButton(label: s.t('wifi_ble_button'), icon: Icons.bluetooth, onPressed: _reconfigureOverBluetooth),
            ]),
          ]),
        ),
      ]),
    );
  }

  String _fmt(DateTime t) =>
      '${t.day.toString().padLeft(2, '0')}.${t.month.toString().padLeft(2, '0')}.${t.year} ${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}

/// Диалог смены Wi‑Fi: название сети и пароль. Поля живут в состоянии диалога (освобождаются вместе с ним).
class _WifiDialog extends StatefulWidget {
  const _WifiDialog();

  @override
  State<_WifiDialog> createState() => _WifiDialogState();
}

class _WifiDialogState extends State<_WifiDialog> {
  final _ssid = TextEditingController();
  final _pass = TextEditingController();

  @override
  void dispose() {
    _ssid.dispose();
    _pass.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = context.read<AppState>().strings;
    return AlertDialog(
      title: Text(s.t('wifi_h')),
      content: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(s.t('wifi_note'), style: TextStyle(color: context.nx.p.textMuted, fontSize: 13)),
          const SizedBox(height: 12),
          TextField(controller: _ssid, maxLength: 32, decoration: InputDecoration(labelText: s.t('wifi_ssid'), counterText: '')),
          const SizedBox(height: 8),
          TextField(controller: _pass, obscureText: true, maxLength: 64, decoration: InputDecoration(labelText: s.t('wifi_pass'), helperText: s.t('wifi_open_hint'), counterText: '')),
        ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(s.t('cancel'))),
        TextButton(onPressed: () => Navigator.pop(context, (ssid: _ssid.text.trim(), password: _pass.text)), child: Text(s.t('wifi_apply'))),
      ],
    );
  }
}
