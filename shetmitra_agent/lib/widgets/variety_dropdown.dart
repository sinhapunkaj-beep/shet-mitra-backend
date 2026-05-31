import 'package:flutter/material.dart';

import '../utils/region_helper.dart';

/// Region-aware variety dropdown.
///
/// Shows the subset of varieties valid for the (region, crop) pair as
/// defined in `RegionHelper.varietiesForRegion`. The widget is purely
/// stateless — the parent owns the selected value.
class VarietyDropdown extends StatelessWidget {
  const VarietyDropdown({
    super.key,
    required this.region,
    required this.crop,
    required this.value,
    required this.onChanged,
    this.label = 'Variety',
  });

  final String region;
  final String crop;
  final String? value;
  final ValueChanged<String?> onChanged;
  final String label;

  @override
  Widget build(BuildContext context) {
    final List<String> varieties =
        RegionHelper.varietiesForRegion(region, crop);

    if (varieties.isEmpty) {
      return const SizedBox.shrink();
    }

    final String? effective =
        (value != null && varieties.contains(value)) ? value : null;

    return DropdownButtonFormField<String>(
      value: effective,
      decoration: InputDecoration(
        labelText: label,
        border: const OutlineInputBorder(),
      ),
      items: varieties
          .map<DropdownMenuItem<String>>(
            (String v) => DropdownMenuItem<String>(value: v, child: Text(v)),
          )
          .toList(),
      onChanged: onChanged,
    );
  }
}
