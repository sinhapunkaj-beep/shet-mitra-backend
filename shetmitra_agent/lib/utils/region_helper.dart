/// Region-aware helpers. The variety list per region is defined in
/// SDD §7.2 and intentionally kept short so the dropdown stays usable
/// on low-end Android phones in low-bandwidth field conditions.
class RegionHelper {
  static const double konkanLatMin = 15.5;
  static const double konkanLatMax = 18.0;
  static const double konkanLngMin = 72.8;
  static const double konkanLngMax = 74.0;

  /// Returns true if (lat, lng) is inside the Konkan coastal bounding
  /// box. Used to auto-suggest Konkani as the default language.
  static bool isInKonkanBbox(double lat, double lng) {
    return lat >= konkanLatMin &&
        lat <= konkanLatMax &&
        lng >= konkanLngMin &&
        lng <= konkanLngMax;
  }

  static const List<String> _allMangoVarieties = <String>[
    'Alphonso',
    'Kesar',
    'Banganapalli',
    'Totapuri',
    'Dasheri',
    'Langra',
  ];

  static const List<String> _grapeVarieties = <String>[
    'Thompson Seedless',
    'Sonaka',
    'Sharad Seedless',
    'Flame Seedless',
  ];

  static const List<String> _pomegranateVarieties = <String>[
    'Bhagwa',
    'Ganesh',
    'Arakta',
    'Mridula',
  ];

  /// Returns the variety options to show for [region] + [crop].
  ///
  /// Region rules (SDD §7.2):
  ///   Konkan + Mango     -> Alphonso, Banganapalli
  ///   Marathwada + Mango -> Kesar, Totapuri
  ///   Nashik + Mango     -> Kesar, Totapuri
  ///   Vidarbha + Mango   -> Dasheri, Langra
  ///   any other region   -> the full set
  static List<String> varietiesForRegion(String region, String crop) {
    final String c = crop.toLowerCase();
    if (c == 'grapes' || c == 'grape') return _grapeVarieties;
    if (c == 'pomegranate') return _pomegranateVarieties;
    if (c != 'mango') return const <String>[];

    return switch (region) {
      'Konkan' => const <String>['Alphonso', 'Banganapalli'],
      'Marathwada' => const <String>['Kesar', 'Totapuri'],
      'Nashik' => const <String>['Kesar', 'Totapuri'],
      'Vidarbha' => const <String>['Dasheri', 'Langra'],
      _ => _allMangoVarieties,
    };
  }

  /// Region-aware crop ordering for the registration dropdown — the
  /// crop most relevant to the region is shown first.
  static List<String> cropsForRegion(String region) {
    return switch (region) {
      'Konkan' => const <String>['Mango', 'Grapes', 'Pomegranate'],
      'Nashik' => const <String>['Grapes', 'Mango', 'Pomegranate'],
      'Marathwada' => const <String>['Pomegranate', 'Mango', 'Grapes'],
      'Vidarbha' => const <String>['Mango', 'Pomegranate', 'Grapes'],
      'Tasgaon' => const <String>['Grapes', 'Pomegranate', 'Mango'],
      _ => const <String>['Grapes', 'Pomegranate', 'Mango'],
    };
  }
}
