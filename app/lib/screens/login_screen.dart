import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../l10n/l10n.dart';
import '../state/app_state.dart';
import '../theme/noctis_theme.dart';
import '../widgets/server_dialog.dart';
import '../widgets/widgets.dart';

/// Вход и регистрация.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _name = TextEditingController();
  final _taps = TapCounter();
  bool _register = false;
  bool _consent = false;
  bool _showPassword = false;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final app = context.read<AppState>();
    if (!(_form.currentState?.validate() ?? false)) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (_register) {
        await app.register(_email.text.trim(), _password.text, _name.text.trim());
      } else {
        await app.login(_email.text.trim(), _password.text);
      }
    } catch (e) {
      if (mounted) setState(() => _error = app.errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = context.s;
    final app = context.watch<AppState>();
    final p = context.nx.p;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 460),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                Row(children: [
                  NoctisLogo(onTap: () {
                    if (_taps.tap()) showServerDialog(context);
                  }),
                  const Spacer(),
                  _LangMenu(app: app),
                  IconButton(
                    tooltip: s.t('theme_label'),
                    onPressed: () => app.setThemeMode(context.nx.dark ? ThemeMode.light : ThemeMode.dark),
                    icon: Icon(context.nx.dark ? Icons.dark_mode_outlined : Icons.light_mode_outlined),
                  ),
                ]),
                const SizedBox(height: 28),
                const Center(child: SectionLabel('NOCTIS', dot: true)),
                const SizedBox(height: 12),
                GlowTitle('${s.t('site_h1a')} ${s.t('site_h1b')}', size: 28, align: TextAlign.center),
                const SizedBox(height: 22),
                GlassCard(
                  padding: const EdgeInsets.all(20),
                  glow: true,
                  child: Form(
                    key: _form,
                    child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                      Row(children: [
                        Expanded(child: _Tab(label: s.t('tab_login'), selected: !_register, onTap: () => setState(() => _register = false))),
                        const SizedBox(width: 8),
                        Expanded(child: _Tab(label: s.t('tab_register'), selected: _register, onTap: () => setState(() => _register = true))),
                      ]),
                      const SizedBox(height: 18),
                      if (_error != null) Notice(_error!),
                      if (_register) ...[
                        TextFormField(controller: _name, decoration: InputDecoration(labelText: s.t('display_name')), textCapitalization: TextCapitalization.words, autofillHints: const [AutofillHints.nickname]),
                        const SizedBox(height: 12),
                      ],
                      TextFormField(
                        controller: _email,
                        decoration: InputDecoration(labelText: s.t('email')),
                        keyboardType: TextInputType.emailAddress,
                        autofillHints: const [AutofillHints.email],
                        validator: (v) => (v == null || !v.contains('@') || !v.contains('.')) ? s.t('email') : null,
                      ),
                      const SizedBox(height: 12),
                      TextFormField(
                        controller: _password,
                        obscureText: !_showPassword,
                        autofillHints: [_register ? AutofillHints.newPassword : AutofillHints.password],
                        decoration: InputDecoration(
                          labelText: _register ? s.t('password') : s.t('password_short'),
                          suffixIcon: IconButton(
                            tooltip: s.t('show_password'),
                            icon: Icon(_showPassword ? Icons.visibility_off_outlined : Icons.visibility_outlined),
                            onPressed: () => setState(() => _showPassword = !_showPassword),
                          ),
                        ),
                        validator: (v) => (v == null || v.length < (_register ? 8 : 1)) ? (_register ? s.t('password') : s.t('password_short')) : null,
                      ),
                      if (_register)
                        CheckboxListTile(
                          value: _consent,
                          onChanged: (v) => setState(() => _consent = v ?? false),
                          contentPadding: EdgeInsets.zero,
                          controlAffinity: ListTileControlAffinity.leading,
                          title: Text(s.t('consent'), style: TextStyle(fontSize: 13, color: p.textMuted)),
                        ),
                      const SizedBox(height: 14),
                      GradientButton(
                        label: _register ? s.t('create_account') : s.t('login'),
                        loading: _busy,
                        onPressed: (_register && !_consent) ? null : _submit,
                      ),
                    ]),
                  ),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

class _Tab extends StatelessWidget {
  const _Tab({required this.label, required this.selected, required this.onTap});
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final nx = context.nx;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        height: 42,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          gradient: selected ? nx.accent : null,
          borderRadius: BorderRadius.circular(999),
          border: selected ? null : Border.all(color: nx.p.glassBorder),
          boxShadow: selected ? nx.glowSm : null,
        ),
        child: Text(label, style: TextStyle(fontWeight: FontWeight.w700, color: selected ? nx.p.onBrand : nx.p.textMuted)),
      ),
    );
  }
}

class _LangMenu extends StatelessWidget {
  const _LangMenu({required this.app});
  final AppState app;

  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<String>(
      tooltip: app.strings.t('language_label'),
      initialValue: app.lang,
      color: context.nx.p.bg2,
      onSelected: app.setLanguage,
      itemBuilder: (_) => [for (final l in kLanguages) PopupMenuItem(value: l, child: Text(kLanguageNames[l]!))],
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.language, size: 20),
          const SizedBox(width: 6),
          Text(app.lang.toUpperCase(), style: TextStyle(fontWeight: FontWeight.w700, color: context.nx.p.textMuted)),
        ]),
      ),
    );
  }
}
