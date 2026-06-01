import 'package:flutter/material.dart';

import '../utils/i18n.dart';
import '../utils/region_helper.dart';

/// Stateless two-option region selector (Maharashtra / Jharkhand) used
/// on the agent login screen per SDD §6.1.
///
/// When a [gpsSuggestion] is provided, the suggested option is annotated
/// with a small "GPS" helper line.
class RegionSelector extends StatelessWidget {
  const RegionSelector({
    super.key,
    required this.value,
    required this.onChanged,
    required this.locale,
    this.gpsSuggestion,
  });

  final String value;
  final ValueChanged<String> onChanged;
  final String locale;
  final String? gpsSuggestion;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(
          I18n.t('select_region', locale),
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: 8),
        _option(
          context,
          code: RegionHelper.regionMH,
          label: I18n.t('region_mh', locale),
          isGps: gpsSuggestion == RegionHelper.regionMH,
        ),
        const SizedBox(height: 8),
        _option(
          context,
          code: RegionHelper.regionJH,
          label: I18n.t('region_jh', locale),
          isGps: gpsSuggestion == RegionHelper.regionJH,
        ),
      ],
    );
  }

  Widget _option(
    BuildContext context, {
    required String code,
    required String label,
    required bool isGps,
  }) {
    final bool selected = value == code;
    final ThemeData theme = Theme.of(context);
    return InkWell(
      borderRadius: BorderRadius.circular(8),
      onTap: () => onChanged(code),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          border: Border.all(
            color: selected
                ? theme.colorScheme.primary
                : theme.colorScheme.outlineVariant,
            width: selected ? 2 : 1,
          ),
          color: selected
              ? theme.colorScheme.primary.withOpacity(0.08)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          children: <Widget>[
            Icon(
              selected ? Icons.radio_button_checked : Icons.radio_button_off,
              color: selected
                  ? theme.colorScheme.primary
                  : theme.colorScheme.outline,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    label,
                    style: TextStyle(
                      fontWeight:
                          selected ? FontWeight.w700 : FontWeight.w500,
                    ),
                  ),
                  if (isGps)
                    Text(
                      I18n.t('gps_suggesting_region', locale),
                      style: TextStyle(
                        fontSize: 11,
                        color: theme.colorScheme.primary,
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
