import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../state/app_state.dart';
import '../theme/noctis_theme.dart';

/// Считает быстрые касания по логотипу: после [needed] касаний подряд (пауза не дольше [gap]) возвращает true.
class TapCounter {
  TapCounter({this.needed = 7, this.gap = const Duration(seconds: 2)});

  final int needed;
  final Duration gap;
  int _count = 0;
  DateTime? _last;

  bool tap([DateTime? now]) {
    final t = now ?? DateTime.now();
    if (_last == null || t.difference(_last!) > gap) _count = 0;
    _last = t;
    if (++_count < needed) return false;
    _count = 0;
    return true;
  }
}

/// Скрытое окно проверки: адрес сервера для тестирования, «Проверить соединение», возврат к адресу по умолчанию.
/// Открывается семью касаниями по логотипу (экран входа и профиль).
Future<void> showServerDialog(BuildContext context) {
  final app = context.read<AppState>();
  return showDialog<void>(context: context, builder: (_) => ChangeNotifierProvider<AppState>.value(value: app, child: const _ServerDialog()));
}

class _ServerDialog extends StatefulWidget {
  const _ServerDialog();

  @override
  State<_ServerDialog> createState() => _ServerDialogState();
}

class _ServerDialogState extends State<_ServerDialog> {
  late final TextEditingController _url = TextEditingController(text: context.read<AppState>().api.baseUrl);
  String? _message;
  bool _ok = false;
  bool _busy = false;

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  Future<void> _test() async {
    final app = context.read<AppState>();
    final s = app.strings;
    if (!AppState.validServerUrl(_url.text)) {
      setState(() {
        _ok = false;
        _message = s.t('conn_bad');
      });
      return;
    }
    setState(() {
      _busy = true;
      _message = null;
    });
    final ok = await app.api.ping(_url.text);
    if (!mounted) return;
    setState(() {
      _busy = false;
      _ok = ok;
      _message = ok ? s.t('conn_ok') : s.t('conn_fail');
    });
  }

  Future<void> _save() async {
    final app = context.read<AppState>();
    if (!AppState.validServerUrl(_url.text)) {
      setState(() {
        _ok = false;
        _message = app.strings.t('conn_bad');
      });
      return;
    }
    await app.setServer(_url.text);
    if (mounted) Navigator.pop(context);
  }

  Future<void> _reset() async {
    final app = context.read<AppState>();
    await app.resetServer();
    if (!mounted) return;
    setState(() {
      _url.text = app.api.baseUrl;
      _ok = true;
      _message = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final s = app.strings;
    final p = context.nx.p;
    return AlertDialog(
      title: Text(s.t('dev_server')),
      content: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          TextField(controller: _url, keyboardType: TextInputType.url, autocorrect: false, enableSuggestions: false),
          const SizedBox(height: 8),
          Text(s.t('dev_hint'), style: TextStyle(color: p.textMuted, fontSize: 12, height: 1.35)),
          if (_message != null) ...[
            const SizedBox(height: 10),
            Text(_message!, key: const Key('server-message'), style: TextStyle(color: _ok ? p.success : p.danger, fontWeight: FontWeight.w600)),
          ],
          const SizedBox(height: 8),
          Wrap(spacing: 8, children: [
            TextButton(onPressed: _busy ? null : _test, child: Text(s.t('test_conn'))),
            TextButton(onPressed: _busy ? null : _reset, child: Text(s.t('reset_default'))),
          ]),
        ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(s.t('cancel'))),
        TextButton(onPressed: _busy ? null : _save, child: Text(s.t('dev_server_save'))),
      ],
    );
  }
}
