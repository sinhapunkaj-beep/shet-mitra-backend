import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../config.dart';
import '../models/agent.dart';
import '../state/agent_state.dart';
import '../state/auth_state.dart';
import '../utils/i18n.dart';
import 'belt_intelligence_screen.dart';
import 'farmers_list_screen.dart';
import 'register_farmer_screen.dart';
import 'settings_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _index = 0;
  String _locale = AppConfig.defaultLocale;

  late final List<Widget Function(String)> _builders = <Widget Function(String)>[
    (String l) => FarmersListScreen(locale: l),
    (String l) => RegisterFarmerScreen(locale: l),
    (String l) => BeltIntelligenceScreen(locale: l),
    (String l) => SettingsScreen(
          locale: l,
          onLocaleChanged: (String code) => setState(() => _locale = code),
        ),
  ];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _refresh());
  }

  Future<void> _refresh() async {
    final AuthState auth = context.read<AuthState>();
    final Agent? agent = auth.currentAgent;
    if (agent == null) return;
    final AgentState st = context.read<AgentState>();
    await st.loadFarmers(agent);
    await st.loadMangoBelt(agent);
  }

  @override
  Widget build(BuildContext context) {
    final AuthState auth = context.watch<AuthState>();
    final Agent? agent = auth.currentAgent;

    if (agent == null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) Navigator.of(context).pushReplacementNamed('/login');
      });
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }

    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: <Widget>[
            Text(I18n.t('app_title', _locale)),
            const SizedBox(width: 12),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                color: Colors.white24,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(
                agent.region,
                style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
              ),
            ),
          ],
        ),
      ),
      body: _builders[_index](_locale),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (int i) => setState(() => _index = i),
        destinations: <NavigationDestination>[
          NavigationDestination(
            icon: const Icon(Icons.people_outline),
            selectedIcon: const Icon(Icons.people),
            label: I18n.t('farmers', _locale),
          ),
          NavigationDestination(
            icon: const Icon(Icons.person_add_alt),
            selectedIcon: const Icon(Icons.person_add),
            label: I18n.t('add_farmer', _locale),
          ),
          NavigationDestination(
            icon: const Icon(Icons.insights_outlined),
            selectedIcon: const Icon(Icons.insights),
            label: I18n.t('belt', _locale),
          ),
          NavigationDestination(
            icon: const Icon(Icons.settings_outlined),
            selectedIcon: const Icon(Icons.settings),
            label: I18n.t('settings', _locale),
          ),
        ],
      ),
    );
  }
}
