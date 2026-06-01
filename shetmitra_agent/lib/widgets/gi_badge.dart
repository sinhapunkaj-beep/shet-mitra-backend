import 'package:flutter/material.dart';

import '../utils/i18n.dart';
import '../utils/region_helper.dart';

/// Gold "Jardalu GI ✅" badge shown on a farm card when the backend
/// has confirmed GI eligibility (`farmer.giVerified == true`). Per
/// SDD §3.3 + §6.3 the badge sits below the variety dropdown on
/// registration and above the area row on the farmer list card.
///
/// A small premium estimate line (1.60× current mandi price) is
/// rendered underneath whenever a reference mandi price is supplied.
class JardaluGiBadge extends StatelessWidget {
  const JardaluGiBadge({
    super.key,
    required this.locale,
    this.referenceMandiPrice,
  });

  final String locale;
  final double? referenceMandiPrice;

  static const Color _gold = Color(0xFFB8860B); // dark goldenrod
  static const Color _goldBg = Color(0xFFFFF8E1);

  @override
  Widget build(BuildContext context) {
    final double? premium = referenceMandiPrice == null
        ? null
        : referenceMandiPrice! * RegionHelper.jardaluGiPremiumMultiplier;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
          decoration: BoxDecoration(
            color: _goldBg,
            border: Border.all(color: _gold, width: 1.2),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const Icon(Icons.workspace_premium,
                  color: _gold, size: 14),
              const SizedBox(width: 4),
              Text(
                I18n.t('jardalu_gi_badge', locale),
                style: const TextStyle(
                  color: _gold,
                  fontWeight: FontWeight.w700,
                  fontSize: 11,
                ),
              ),
            ],
          ),
        ),
        if (premium != null) ...<Widget>[
          const SizedBox(height: 2),
          Text(
            '${I18n.t('gi_premium_estimate', locale)}: '
            '₹${premium.toStringAsFixed(0)}/kg',
            style: const TextStyle(fontSize: 10, color: _gold),
          ),
        ],
      ],
    );
  }
}
