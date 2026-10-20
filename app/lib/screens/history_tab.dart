import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../l10n/l10n.dart';
import '../models/models.dart';
import '../state/app_state.dart';
import '../theme/noctis_theme.dart';
import '../widgets/widgets.dart';

/// История диалогов с колонкой.
class HistoryTab extends StatefulWidget {
  const HistoryTab({super.key, required this.device});
  final Device device;

  @override
  State<HistoryTab> createState() => _HistoryTabState();
}

class _HistoryTabState extends State<HistoryTab> {
  List<HistoryItem>? _items;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final app = context.read<AppState>();
    try {
      final list = await app.api.history(widget.device.publicId);
      if (mounted) {
        setState(() {
          _items = list;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = app.errorText(e));
    }
  }

  Future<void> _clear() async {
    final app = context.read<AppState>();
    final s = app.strings;
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        content: Text(s.t('clear_confirm')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: Text(s.t('cancel'))),
          TextButton(onPressed: () => Navigator.pop(c, true), child: Text(s.t('delete'))),
        ],
      ),
    );
    if (yes != true) return;
    try {
      await app.api.clearHistory(widget.device.publicId);
      await _load();
    } catch (e) {
      if (mounted) setState(() => _error = app.errorText(e));
    }
  }

  String _fmt(DateTime? t) => t == null
      ? ''
      : '${t.day.toString().padLeft(2, '0')}.${t.month.toString().padLeft(2, '0')} ${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) {
    final s = context.s;
    final p = context.nx.p;
    final items = _items;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 24), children: [
        if (_error != null) Notice(_error!),
        GlassCard(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(child: Text(s.t('tab_history'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800))),
              GhostButton(label: s.t('clear_history'), icon: Icons.delete_outline, danger: true, small: true, onPressed: (items == null || items.isEmpty) ? null : _clear),
            ]),
            const SizedBox(height: 10),
            if (items == null)
              Text(s.t('loading'), style: TextStyle(color: p.textMuted))
            else if (items.isEmpty)
              Center(child: Padding(padding: const EdgeInsets.all(12), child: Text(s.t('no_history'), style: TextStyle(color: p.textFaint))))
            else
              for (final h in items)
                Container(
                  width: double.infinity,
                  margin: const EdgeInsets.only(top: 8),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(color: p.bg1, borderRadius: BorderRadius.circular(10), border: Border.all(color: p.glassBorder)),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(_fmt(h.createdAt), style: TextStyle(color: p.textFaint, fontSize: 11.5)),
                    const SizedBox(height: 2),
                    Text('${s.t('you')}: ${h.userText}', style: const TextStyle(fontWeight: FontWeight.w700)),
                    const SizedBox(height: 2),
                    Text('${s.t('assistant')}: ${h.assistantText}', style: TextStyle(color: p.textMuted)),
                  ]),
                ),
          ]),
        ),
      ]),
    );
  }
}
