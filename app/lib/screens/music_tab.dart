import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/models.dart';
import '../state/app_state.dart';
import '../theme/noctis_theme.dart';
import '../widgets/widgets.dart';

/// Музыка: плеер колонки, библиотека, радио, Jamendo.
class MusicTab extends StatefulWidget {
  const MusicTab({super.key, required this.device});
  final Device device;

  @override
  State<MusicTab> createState() => _MusicTabState();
}

class _MusicTabState extends State<MusicTab> {
  PlayerInfo? _player;
  List<Track>? _tracks;
  String _source = 'local';
  final _query = TextEditingController();
  final _stationName = TextEditingController();
  final _stationUrl = TextEditingController();
  Timer? _poll;
  String? _error;
  String? _ok;
  bool _busy = false;

  String get _id => widget.device.publicId;

  @override
  void initState() {
    super.initState();
    _pollPlayer();
    _search();
    _poll = Timer.periodic(const Duration(seconds: 3), (_) => _pollPlayer());
  }

  @override
  void dispose() {
    _poll?.cancel();
    _query.dispose();
    _stationName.dispose();
    _stationUrl.dispose();
    super.dispose();
  }

  Future<void> _pollPlayer() async {
    try {
      final info = await context.read<AppState>().api.player(_id);
      if (mounted) setState(() => _player = info);
    } catch (_) {
      // повтор через 3 секунды
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    final app = context.read<AppState>();
    setState(() {
      _error = null;
      _ok = null;
    });
    try {
      await action();
    } catch (e) {
      if (mounted) setState(() => _error = app.errorText(e));
    }
  }

  Future<void> _search() => _run(() async {
        final list = await context.read<AppState>().api.tracks(_source, _query.text.trim());
        if (mounted) setState(() => _tracks = list);
      });

  Future<void> _upload() async {
    final app = context.read<AppState>();
    final s = app.strings;
    final res = await FilePicker.platform.pickFiles(allowMultiple: true, type: FileType.custom, allowedExtensions: const ['mp3', 'flac', 'ogg', 'wav'], withData: true);
    if (res == null || res.files.isEmpty) return;
    setState(() => _busy = true);
    await _run(() async {
      for (final f in res.files) {
        if (f.bytes != null) await app.api.uploadTrack(f.name, f.bytes!);
      }
      if (mounted) setState(() => _ok = s.t('uploaded', {'n': '${res.files.length}'}));
      _source = 'local';
      await _search();
    });
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _addStation() async {
    final app = context.read<AppState>();
    if (_stationName.text.trim().isEmpty || _stationUrl.text.trim().isEmpty) return;
    await _run(() async {
      await app.api.addStation(_stationName.text.trim(), _stationUrl.text.trim());
      _stationName.clear();
      _stationUrl.clear();
      if (mounted) setState(() => _ok = app.strings.t('saved'));
      if (_source == 'radio') await _search();
    });
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final s = app.strings;
    final p = context.nx.p;
    final player = _player;
    final online = widget.device.online;

    return RefreshIndicator(
      onRefresh: () async {
        await _pollPlayer();
        await _search();
      },
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 24), children: [
        if (!online) Notice(s.t('offline_note'), kind: NoticeKind.warn),
        if (_error != null) Notice(_error!),
        if (_ok != null) Notice(_ok!, kind: NoticeKind.ok),
        GlassCard(
          glow: player?.active ?? false,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(s.t('now_playing'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
            const SizedBox(height: 10),
            if (player == null || !player.active)
              Text(s.t('nothing_playing'), style: TextStyle(color: p.textMuted))
            else ...[
              Text(player.title ?? '', style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
              Text('${player.artist ?? ''} · ${player.index + 1}/${player.total}', style: TextStyle(color: p.textMuted, fontSize: 13)),
              const SizedBox(height: 8),
              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                IconButton(tooltip: s.t('prev'), iconSize: 30, icon: const Icon(Icons.skip_previous), onPressed: () => _control('prev')),
                IconButton(
                  tooltip: player.playing ? s.t('pause') : s.t('resume'),
                  iconSize: 44,
                  color: p.violet300,
                  icon: Icon(player.playing ? Icons.pause_circle_filled : Icons.play_circle_fill),
                  onPressed: () => _control(player.playing ? 'pause' : 'resume'),
                ),
                IconButton(tooltip: s.t('next'), iconSize: 30, icon: const Icon(Icons.skip_next), onPressed: () => _control('next')),
                IconButton(tooltip: s.t('stop'), iconSize: 30, icon: const Icon(Icons.stop), onPressed: () => _control('stop')),
              ]),
            ],
          ]),
        ),
        const SizedBox(height: 14),
        GlassCard(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(s.t('find_music'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
            const SizedBox(height: 12),
            SegmentedButton<String>(
              showSelectedIcon: false,
              segments: [
                ButtonSegment(value: 'local', label: Text(s.t('source_library'), overflow: TextOverflow.ellipsis)),
                ButtonSegment(value: 'radio', label: Text(s.t('source_radio'))),
                ButtonSegment(value: 'jamendo', label: Text(s.t('source_jamendo'))),
              ],
              selected: {_source},
              onSelectionChanged: (v) {
                setState(() {
                  _source = v.first;
                  _tracks = null;
                });
                _search();
              },
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _query,
              textInputAction: TextInputAction.search,
              onSubmitted: (_) => _search(),
              decoration: InputDecoration(labelText: s.t('query'), suffixIcon: IconButton(icon: const Icon(Icons.search), onPressed: _search)),
            ),
            if (_source == 'local') ...[
              const SizedBox(height: 12),
              GhostButton(label: s.t('upload'), icon: Icons.upload_file, onPressed: _busy ? null : _upload),
            ],
            const SizedBox(height: 12),
            if (_tracks == null)
              Text(s.t('loading'), style: TextStyle(color: p.textMuted))
            else if (_tracks!.isEmpty)
              Center(child: Padding(padding: const EdgeInsets.all(10), child: Text(s.t('no_tracks'), style: TextStyle(color: p.textFaint))))
            else
              for (final t in _tracks!)
                Container(
                  margin: const EdgeInsets.only(top: 8),
                  padding: const EdgeInsets.fromLTRB(14, 6, 4, 6),
                  decoration: BoxDecoration(color: p.bg1, borderRadius: BorderRadius.circular(10), border: Border.all(color: p.glassBorder)),
                  child: Row(children: [
                    Expanded(
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(t.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w700)),
                        if (t.artist.isNotEmpty || t.license.isNotEmpty) Text([t.artist, t.license].where((x) => x.isNotEmpty).join(' · '), maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: p.textMuted, fontSize: 12)),
                      ]),
                    ),
                    IconButton(
                      tooltip: s.t('play'),
                      icon: Icon(Icons.play_circle_outline, color: online ? p.violet300 : p.textFaint),
                      onPressed: online ? () => _play(t) : null,
                    ),
                    if (_source != 'jamendo') IconButton(tooltip: s.t('delete'), icon: Icon(Icons.delete_outline, color: p.danger), onPressed: () => _run(() async {
                          await app.api.deleteTrack(t);
                          await _search();
                        })),
                  ]),
                ),
          ]),
        ),
        const SizedBox(height: 14),
        GlassCard(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(s.t('add_station'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
            const SizedBox(height: 4),
            Text(s.t('radio_hint'), style: TextStyle(color: p.textMuted, fontSize: 13)),
            const SizedBox(height: 12),
            TextField(controller: _stationName, decoration: InputDecoration(labelText: s.t('station_name'))),
            const SizedBox(height: 10),
            TextField(controller: _stationUrl, keyboardType: TextInputType.url, decoration: InputDecoration(labelText: s.t('station_url'), hintText: 'https://…/stream.mp3')),
            const SizedBox(height: 12),
            GhostButton(label: s.t('add_station'), icon: Icons.radio, onPressed: _addStation),
          ]),
        ),
      ]),
    );
  }

  Future<void> _control(String action) => _run(() async {
        final info = await context.read<AppState>().api.playerAction(_id, action);
        if (mounted) setState(() => _player = info);
      });

  Future<void> _play(Track t) => _run(() async {
        final info = await context.read<AppState>().api.play(_id, t.id);
        if (mounted) setState(() => _player = info);
      });
}
