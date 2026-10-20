import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../config.dart';
import '../l10n/l10n.dart';
import '../models/models.dart';
import '../services/pairing.dart';
import '../state/app_state.dart';
import '../theme/noctis_theme.dart';
import '../theme/tokens.g.dart';
import '../widgets/widgets.dart';

enum _Step { intro, pick, code, wifi, working, done }

/// Мастер добавления колонки: Bluetooth → код с экрана → Wi‑Fi → привязка к учётной записи.
///
/// С [reconfigure] — повторная настройка существующей колонки (другой Wi‑Fi и/или адрес сервера) без сброса платы:
/// устройство на сервере не создаётся и не удаляется, колонка сохраняет свой device_id и токен.
class PairingScreen extends StatefulWidget {
  const PairingScreen({super.key, this.reconfigure});
  final Device? reconfigure;

  @override
  State<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends State<PairingScreen> {
  _Step _step = _Step.intro;
  final _name = TextEditingController();
  final _code = TextEditingController();
  final _ssid = TextEditingController();
  final _wifiPassword = TextEditingController();
  List<FoundSpeaker> _found = [];
  FoundSpeaker? _picked;
  bool _busy = false;
  bool _showPassword = false;
  String? _error;
  String _progress = '';
  bool _connected = false;

  late final PairingTransport _pairing;

  @override
  void initState() {
    super.initState();
    _pairing = context.read<AppState>().pairing;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _name.text.isEmpty) _name.text = context.read<AppState>().strings.t('pair_default_name');
    });
  }

  @override
  void dispose() {
    _name.dispose();
    _code.dispose();
    _ssid.dispose();
    _wifiPassword.dispose();
    _pairing.close();
    super.dispose();
  }

  Future<void> _scan() async {
    final s = context.read<AppState>().strings;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (!await _pairing.ensurePermissions()) {
        setState(() => _error = s.t('pair_perm_denied'));
        return;
      }
      final list = await _pairing.scan();
      if (!mounted) return;
      setState(() {
        _found = list;
        _step = _Step.pick;
      });
    } catch (_) {
      if (mounted) setState(() => _error = s.t('pair_failed'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Основная последовательность: сеанс → создание устройства на сервере → адрес сервера → Wi‑Fi → ожидание «онлайн».
  Future<void> _run() async {
    final app = context.read<AppState>();
    final s = app.strings;
    final speaker = _picked!;
    setState(() {
      _step = _Step.working;
      _error = null;
      _progress = s.t('pair_working');
    });
    String? createdId;
    var linkSent = false;  // после передачи платы идентичности удалять устройство нельзя: плата осталась бы с «мёртвым» токеном (403)
    final target = widget.reconfigure;
    try {
      await _pairing.connect(speaker, _code.text.trim());
      final board = serverForBoard(app.api.baseUrl);
      if (target != null) {
        // повторная настройка: только новый адрес сервера и Wi‑Fi, идентичность платы остаётся
        await _pairing.sendLink(server: board.server, tls: board.tls);
        linkSent = true;
        createdId = target.publicId;
      } else {
        final created = await app.api.createDevice(_name.text.trim().isEmpty ? s.t('pair_default_name') : _name.text.trim(), await app.localTimezone());
        createdId = created.device.publicId;
        await _pairing.sendLink(server: board.server, tls: board.tls, deviceId: createdId, token: created.token);
        linkSent = true;
      }
      await _pairing.sendWifi(_ssid.text.trim(), _wifiPassword.text);
      if (mounted) setState(() => _progress = s.t('pair_waiting'));
      var online = false;
      for (var i = 0; i < 45 && mounted; i++) {
        await Future<void>.delayed(const Duration(seconds: 2));
        await app.refreshDevices();
        if (app.devices.any((d) => d.publicId == createdId && d.online)) {
          online = true;
          break;
        }
      }
      app.selectDevice(createdId);
      if (!mounted) return;
      setState(() {
        _connected = online;
        _step = _Step.done;
      });
    } on PairingException catch (e) {
      if (createdId != null && widget.reconfigure == null && !linkSent) await app.api.deleteDevice(createdId).catchError((_) {});
      if (!mounted) return;
      setState(() {
        _error = e.wrongCode ? s.t('pair_wrong_code') : '${s.t('pair_failed')} (${e.message})';
        _step = e.wrongCode ? _Step.code : _Step.wifi;
      });
    } catch (e) {
      if (createdId != null && widget.reconfigure == null && !linkSent) await app.api.deleteDevice(createdId).catchError((_) {});
      if (!mounted) return;
      setState(() {
        _error = e is Exception && e.toString().contains('ApiException') ? app.errorText(e) : s.t('pair_failed');
        _step = _Step.wifi;
      });
    } finally {
      await _pairing.close();
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = context.s;
    final p = context.nx.p;
    return Scaffold(
      appBar: AppBar(title: Text(widget.reconfigure == null ? s.t('pair_title') : s.t('re_title'), style: const TextStyle(fontWeight: FontWeight.w800)), leading: _step == _Step.working ? const SizedBox() : null),
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 24), children: [
          if (kMockBle) Notice(s.t('pair_demo'), kind: NoticeKind.warn),
          if (_error != null) Notice(_error!),
          GlassCard(
            glow: true,
            padding: const EdgeInsets.all(20),
            child: AnimatedSwitcher(duration: const Duration(milliseconds: 200), child: KeyedSubtree(key: ValueKey(_step), child: _body(s, p))),
          ),
        ]),
      ),
    );
  }

  Widget _body(Strings s, NoctisPalette p) {
    switch (_step) {
      case _Step.intro:
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Center(child: Icon(Icons.bluetooth_searching, size: 56, color: p.violet300)),
          const SizedBox(height: 14),
          Text(widget.reconfigure == null ? s.t('pair_intro') : s.t('re_intro'), style: TextStyle(color: p.textMuted, height: 1.45)),
          const SizedBox(height: 18),
          if (widget.reconfigure == null) LabeledField(label: s.t('pair_name'), child: TextField(controller: _name, maxLength: 80, decoration: const InputDecoration(counterText: ''))),
          GradientButton(label: _busy ? s.t('pair_scanning') : s.t('pair_scan'), icon: Icons.search, loading: _busy, onPressed: _scan),
        ]);
      case _Step.pick:
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(s.t('pair_pick'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
          const SizedBox(height: 12),
          if (_found.isEmpty) Notice(s.t('pair_none'), kind: NoticeKind.warn),
          for (final f in _found)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: GlassCard(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                onTap: () => setState(() {
                  _picked = f;
                  _step = _Step.code;
                  _error = null;
                }),
                child: Row(children: [
                  Icon(Icons.speaker, color: p.violet300),
                  const SizedBox(width: 12),
                  Expanded(child: Text(f.name, style: const TextStyle(fontWeight: FontWeight.w700))),
                  Icon(Icons.chevron_right, color: p.textMuted),
                ]),
              ),
            ),
          const SizedBox(height: 8),
          GhostButton(label: s.t('pair_scan'), icon: Icons.refresh, onPressed: _busy ? null : _scan),
        ]);
      case _Step.code:
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(s.t('pair_code_title'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
          const SizedBox(height: 4),
          Text(_picked?.name ?? '', style: TextStyle(color: p.textMuted)),
          const SizedBox(height: 14),
          TextField(
            controller: _code,
            keyboardType: TextInputType.number,
            maxLength: 8,
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 30, letterSpacing: 8, fontWeight: FontWeight.w800),
            inputFormatters: [FilteringTextInputFormatter.digitsOnly],
            decoration: InputDecoration(hintText: '••••••••', helperText: s.t('pair_code_hint'), counterText: ''),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 14),
          GradientButton(label: s.t('next_step'), onPressed: _code.text.trim().length == 8 ? () => setState(() => _step = _Step.wifi) : null),
        ]);
      case _Step.wifi:
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(s.t('pair_wifi_title'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
          const SizedBox(height: 4),
          Text(s.t('pair_wifi_note'), style: TextStyle(color: p.textMuted, fontSize: 13)),
          const SizedBox(height: 14),
          TextField(controller: _ssid, decoration: InputDecoration(labelText: s.t('pair_ssid')), onChanged: (_) => setState(() {})),
          const SizedBox(height: 12),
          TextField(
            controller: _wifiPassword,
            obscureText: !_showPassword,
            decoration: InputDecoration(
              labelText: s.t('pair_wifi_password'),
              suffixIcon: IconButton(icon: Icon(_showPassword ? Icons.visibility_off_outlined : Icons.visibility_outlined), onPressed: () => setState(() => _showPassword = !_showPassword)),
            ),
          ),
          const SizedBox(height: 16),
          GradientButton(label: s.t('pair_connect'), icon: Icons.link, onPressed: _ssid.text.trim().isEmpty ? null : _run),
        ]);
      case _Step.working:
        return Column(children: [
          const SizedBox(height: 12),
          const SizedBox(width: 44, height: 44, child: CircularProgressIndicator(strokeWidth: 3)),
          const SizedBox(height: 18),
          Text(_progress, textAlign: TextAlign.center, style: const TextStyle(fontWeight: FontWeight.w700)),
          const SizedBox(height: 12),
        ]);
      case _Step.done:
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Center(child: Icon(_connected ? Icons.check_circle_outline : Icons.hourglass_bottom, size: 60, color: _connected ? p.success : p.warning)),
          const SizedBox(height: 12),
          Text(_connected ? (widget.reconfigure == null ? s.t('pair_done') : s.t('re_done')) : s.t('pair_timeout'), textAlign: TextAlign.center, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700, height: 1.4)),
          const SizedBox(height: 18),
          GradientButton(label: s.t('ok'), onPressed: () => Navigator.of(context).pop()),
        ]);
    }
  }
}
