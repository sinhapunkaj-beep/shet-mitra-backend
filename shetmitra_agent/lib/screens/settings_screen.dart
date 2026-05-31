import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/agent.dart';
import '../state/auth_state.dart';
import '../utils/i18n.dart';
import '../widgets/language_selector.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({
    super.key,
    required this.locale,
    required this.onLocaleChanged,
  });

  final String locale;
  final ValueChanged<String> onLocaleChanged;

  @override
  Widget build(BuildContext context) {
    final AuthState auth = context.watch<AuthState>();
    final Agent? agent = auth.currentAgent;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: <Widget>[
        Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(I18n.t('language', locale),
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 12),
                LanguageSelector(
                  value: locale,
                  onChanged: (String? v) {
                    if (v != null) onLocaleChanged(v);
                  },
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        if (agent != null)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text('Agent profile',
                      style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  _row('Name', agent.name),
                  _row('Mobile', agent.mobile),
                  _row('Region', agent.region),
                  _row('Districts', agent.districts.join(', ')),
                  _row('Status', agent.isActive ? 'Active' : 'Inactive'),
                ],
              ),
            ),
          ),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          icon: const Icon(Icons.logout),
          label: Text(I18n.t('logout', locale)),
          onPressed: () async {
            await context.read<AuthState>().logout();
            if (context.mounted) {
              Navigator.of(context).pushReplacementNamed('/login');
            }
          },
        ),
      ],
    );
  }

  Widget _row(String k, String v) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          children: <Widget>[
            SizedBox(
              width: 90,
              child: Text(k, style: const TextStyle(fontWeight: FontWeight.w600)),
            ),
            Expanded(child: Text(v)),
          ],
        ),
      );
}
