import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/agent.dart';
import '../models/mango_belt.dart';
import '../state/agent_state.dart';
import '../state/auth_state.dart';
import '../utils/i18n.dart';

class BeltIntelligenceScreen extends StatefulWidget {
  const BeltIntelligenceScreen({super.key, required this.locale});

  final String locale;

  @override
  State<BeltIntelligenceScreen> createState() => _BeltIntelligenceScreenState();
}

class _BeltIntelligenceScreenState extends State<BeltIntelligenceScreen>
    with SingleTickerProviderStateMixin {
  TabController? _tabs;
  List<String> _regions = const <String>[];

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final Agent? agent = context.read<AuthState>().currentAgent;
    if (agent == null) return;
    // Each agent currently owns a single region; the tab bar shows that
    // region plus a synthetic "All" tab as a convenience.
    final List<String> regions = <String>[agent.region];
    if (_regions.length != regions.length || _regions.first != regions.first) {
      _tabs?.dispose();
      _tabs = TabController(length: regions.length, vsync: this);
      _regions = regions;
    }
  }

  @override
  void dispose() {
    _tabs?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final Agent? agent = context.watch<AuthState>().currentAgent;
    final AgentState st = context.watch<AgentState>();
    if (agent == null || _tabs == null) {
      return const Center(child: CircularProgressIndicator());
    }

    return Column(
      children: <Widget>[
        TabBar(
          controller: _tabs,
          tabs: _regions.map((String r) => Tab(text: r)).toList(),
          labelColor: Theme.of(context).colorScheme.primary,
        ),
        Expanded(
          child: TabBarView(
            controller: _tabs,
            children: _regions.map((String r) {
              final List<MangoBelt> rows = st.mangoBelt
                  .where((MangoBelt b) => b.region == r)
                  .toList();
              if (rows.isEmpty) {
                return Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(
                      'No mango belt data yet for $r.\n'
                      'Data is populated by the AMED pipeline.',
                      textAlign: TextAlign.center,
                    ),
                  ),
                );
              }
              return ListView.builder(
                padding: const EdgeInsets.all(12),
                itemCount: rows.length,
                itemBuilder: (BuildContext c, int i) => _BeltCard(
                  belt: rows[i],
                  locale: widget.locale,
                ),
              );
            }).toList(),
          ),
        ),
      ],
    );
  }
}

class _BeltCard extends StatelessWidget {
  const _BeltCard({required this.belt, required this.locale});

  final MangoBelt belt;
  final String locale;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Text(
                  belt.variety,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const Spacer(),
                if (belt.bearingYear != null)
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 8, vertical: 4),
                    decoration: BoxDecoration(
                      color: _bearingColor(belt.bearingYear!).withOpacity(0.15),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                          color: _bearingColor(belt.bearingYear!)
                              .withOpacity(0.4)),
                    ),
                    child: Text(
                      belt.bearingYear!,
                      style: TextStyle(
                        color: _bearingColor(belt.bearingYear!),
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 8),
            Text('Region: ${belt.region}'),
            Text('Fields: ${belt.totalFields}'),
            Text('Acres: ${belt.totalAreaAcres.toStringAsFixed(1)}'),
            Text(
                'Est. volume: ${belt.estimatedVolumeMt.toStringAsFixed(1)} MT'),
          ],
        ),
      ),
    );
  }

  Color _bearingColor(String b) => switch (b) {
        'ON' => Colors.green,
        'OFF' => Colors.red,
        _ => Colors.grey,
      };
}
