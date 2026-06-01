import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/agent.dart';
import '../state/agent_state.dart';
import '../state/auth_state.dart';
import '../utils/i18n.dart';
import '../utils/region_helper.dart';
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
  String? _locale;

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

    // If the user has not picked a locale yet, default it from the
    // agent's region (JH → Hindi, MH → Marathi).
    final String locale = _locale ??
        RegionHelper.defaultLocaleForRegionCode(agent.regionCode);
    final String brand = RegionHelper.brandForRegionCode(agent.regionCode);

    return Scaffold(
      appBar: AppBar(
        // Forest-green scheme is already the seed colour. Keep both
        // brands on the same colour scheme per SDD §6.3.
        title: Row(
          children: <Widget>[
            Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  brand,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                Text(
                  I18n.t('app_subtitle', locale),
                  style: const TextStyle(
                      fontSize: 11, fontWeight: FontWeight.w400),
                ),
              ],
            ),
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
      body: _builders[_index](locale),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (int i) => setState(() => _index = i),
        destinations: <NavigationDestination>[
          NavigationDestination(
            icon: const Icon(Icons.people_outline),
            selectedIcon: const Icon(Icons.people),
            label: I18n.t('farmers', locale),
          ),
          NavigationDestination(
            icon: const Icon(Icons.person_add_alt),
            selectedIcon: const Icon(Icons.person_add),
            label: I18n.t('add_farmer', locale),
          ),
          NavigationDestination(
            icon: const Icon(Icons.insights_outlined),
            selectedIcon: const Icon(Icons.insights),
            label: I18n.t('belt', locale),
          ),
          NavigationDestination(
            icon: const Icon(Icons.settings_outlined),
            selectedIcon: const Icon(Icons.settings),
            label: I18n.t('settings', locale),
          ),
        ],
      ),
    );
  }
}
