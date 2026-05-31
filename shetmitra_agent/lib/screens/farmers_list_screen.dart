import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/agent.dart';
import '../models/farmer.dart';
import '../state/agent_state.dart';
import '../state/auth_state.dart';
import '../utils/i18n.dart';
import '../widgets/crop_chip.dart';

class FarmersListScreen extends StatefulWidget {
  const FarmersListScreen({super.key, required this.locale});

  final String locale;

  @override
  State<FarmersListScreen> createState() => _FarmersListScreenState();
}

class _FarmersListScreenState extends State<FarmersListScreen> {
  String _cropFilter = 'All';

  Future<void> _refresh() async {
    final Agent? agent = context.read<AuthState>().currentAgent;
    if (agent == null) return;
    await context.read<AgentState>().loadFarmers(agent);
  }

  @override
  Widget build(BuildContext context) {
    final AgentState st = context.watch<AgentState>();
    final List<Farmer> all = st.farmersInTerritory;
    final Set<String> crops = <String>{
      'All',
      ...all.map((Farmer f) => f.currentCrop).where((String c) => c.isNotEmpty),
    };

    final List<Farmer> filtered = _cropFilter == 'All'
        ? all
        : all.where((Farmer f) => f.currentCrop == _cropFilter).toList();

    return Column(
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: <Widget>[
              Text('${I18n.t('crop', widget.locale)}: '),
              const SizedBox(width: 8),
              DropdownButton<String>(
                value: _cropFilter,
                items: crops
                    .map<DropdownMenuItem<String>>(
                      (String c) =>
                          DropdownMenuItem<String>(value: c, child: Text(c)),
                    )
                    .toList(),
                onChanged: (String? v) {
                  if (v != null) setState(() => _cropFilter = v);
                },
              ),
              const Spacer(),
              Text('${filtered.length} / ${all.length}'),
            ],
          ),
        ),
        if (st.loadingFarmers) const LinearProgressIndicator(minHeight: 2),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _refresh,
            child: filtered.isEmpty
                ? ListView(
                    children: const <Widget>[
                      SizedBox(height: 200),
                      Center(child: Text('No farmers in this territory yet.')),
                    ],
                  )
                : ListView.separated(
                    itemCount: filtered.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (BuildContext ctx, int i) {
                      final Farmer f = filtered[i];
                      return ListTile(
                        title: Text(
                          f.fullName,
                          style: const TextStyle(fontWeight: FontWeight.w600),
                        ),
                        subtitle: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text('${f.village} · ${f.district}'),
                            Text(
                                '${f.areaAcres.toStringAsFixed(2)} ${I18n.t('area_acres', widget.locale)}'),
                          ],
                        ),
                        trailing: CropChip(
                          crop: f.currentCrop,
                          variety: f.currentCropVariety,
                        ),
                        isThreeLine: true,
                      );
                    },
                  ),
          ),
        ),
      ],
    );
  }
}
