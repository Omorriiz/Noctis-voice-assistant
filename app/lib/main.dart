import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'config.dart';
import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'services/api_client.dart';
import 'services/ble_pairing.dart';
import 'services/pairing.dart';
import 'services/token_store.dart';
import 'state/app_state.dart';
import 'theme/noctis_theme.dart';
import 'widgets/widgets.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  final tokens = SecureTokenStore();
  final api = ApiClient(baseUrl: prefs.getString('server') ?? kServerDefault, tokens: tokens);
  final PairingTransport pairing = kMockBle ? MockPairing() : BlePairing();
  final state = AppState(api: api, prefs: prefs, tokens: tokens, pairing: pairing, systemLanguage: ui.PlatformDispatcher.instance.locale.languageCode);
  runApp(NoctisApp(state: state));
  state.start();
}

class NoctisApp extends StatelessWidget {
  const NoctisApp({super.key, required this.state});
  final AppState state;

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider<AppState>.value(
      value: state,
      child: Consumer<AppState>(
        builder: (context, app, _) => MaterialApp(
          title: 'Noctis',
          debugShowCheckedModeBanner: false,
          themeMode: app.themeMode,
          theme: NoctisTheme.build(dark: false),
          darkTheme: NoctisTheme.build(dark: true),
          locale: Locale(app.lang),
          supportedLocales: [for (final l in ['ru', 'en', 'es', 'de', 'fr']) Locale(l)],
          localizationsDelegates: GlobalMaterialLocalizations.delegates,
          builder: (context, child) => CosmosBackground(child: child ?? const SizedBox()),
          home: switch (app.status) {
            AuthStatus.unknown => const _Splash(),
            AuthStatus.signedOut => const LoginScreen(),
            AuthStatus.signedIn => const HomeShell(),
          },
        ),
      ),
    );
  }
}

class _Splash extends StatelessWidget {
  const _Splash();

  @override
  Widget build(BuildContext context) => const Scaffold(body: Center(child: NoctisLogo(size: 64)));
}
