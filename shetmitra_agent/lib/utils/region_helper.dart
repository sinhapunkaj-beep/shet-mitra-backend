/// Region-aware helpers. The variety list per region is defined in
/// SDD §7.2 and intentionally kept short so the dropdown stays usable
/// on low-end Android phones in low-bandwidth field conditions.
///
/// Bagaan Sathi extension (SDD §1, §3.2, §3.3):
///   * Top-level "region" can now be MH (Maharashtra / ShetMitra) or
///     JH (Jharkhand / Bagaan Sathi). The agent picks this at login and
///     it is auto-suggested from GPS.
///   * Within MH we keep the existing Konkan / Marathwada / Nashik /
///     Vidarbha / Tasgaon sub-regions used for variety routing.
///   * Within JH the variety list shifts to Mallika / Amrapali / Jardalu
///     / Himsagar / Langra and Jardalu unlocks a GI Zone badge inside
///     the Bhagalpur-Godda bbox.
class RegionHelper {
  // ── Top-level region codes ────────────────────────────────────────
  /// Maharashtra (ShetMitra brand).
  static const String regionMH = 'MH';

  /// Jharkhand (Bagaan Sathi brand).
  static const String regionJH = 'JH';

  /// All supported top-level region codes.
  static const List<String> supportedRegions = <String>[regionMH, regionJH];

  // ── Konkan coastal bbox (sub-region inside MH) ────────────────────
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

  // ── Maharashtra bbox (top-level region) ───────────────────────────
  static const double mhLatMin = 15.5;
  static const double mhLatMax = 22.0;
  static const double mhLngMin = 72.5;
  static const double mhLngMax = 80.5;

  /// Returns true if (lat, lng) is inside the Maharashtra bbox.
  static bool isInMaharashtraBbox(double lat, double lng) {
    return lat >= mhLatMin &&
        lat <= mhLatMax &&
        lng >= mhLngMin &&
        lng <= mhLngMax;
  }

  // ── Jharkhand bbox (top-level region) ─────────────────────────────
  static const double jhLatMin = 21.5;
  static const double jhLatMax = 25.0;
  static const double jhLngMin = 83.5;
  static const double jhLngMax = 87.5;

  /// Returns true if (lat, lng) is inside the Jharkhand bbox.
  static bool isInJharkhandBbox(double lat, double lng) {
    return lat >= jhLatMin &&
        lat <= jhLatMax &&
        lng >= jhLngMin &&
        lng <= jhLngMax;
  }

  // ── Jardalu GI zone bbox (sub-region inside JH) ───────────────────
  /// Bhagalpur / Godda belt — varieties grown here AND registered as
  /// Jardalu are eligible for the GI Zone badge (SDD §3.3).
  static const double jardaluGiLatMin = 24.0;
  static const double jardaluGiLatMax = 25.0;
  static const double jardaluGiLngMin = 86.5;
  static const double jardaluGiLngMax = 87.5;

  /// Returns true if (lat, lng) falls inside the Jardalu GI bbox.
  static bool isInJardaluGiBbox(double lat, double lng) {
    return lat >= jardaluGiLatMin &&
        lat <= jardaluGiLatMax &&
        lng >= jardaluGiLngMin &&
        lng <= jardaluGiLngMax;
  }

  /// Returns the suggested top-level region (`MH` / `JH`) for a GPS
  /// fix, or `null` if the device is outside both bboxes. Jharkhand
  /// wins if a point lies inside both (overlap is geographically
  /// impossible but defensive logic helps in mock locations).
  static String? suggestTopLevelRegion(double lat, double lng) {
    if (isInJharkhandBbox(lat, lng)) return regionJH;
    if (isInMaharashtraBbox(lat, lng)) return regionMH;
    return null;
  }

  /// True if the (lat, lng, variety) tuple qualifies for the Jardalu
  /// GI badge.
  static bool isJardaluGiEligible({
    required String variety,
    required double? lat,
    required double? lng,
  }) {
    if (variety.toLowerCase() != 'jardalu') return false;
    if (lat == null || lng == null) return false;
    return isInJardaluGiBbox(lat, lng);
  }

  /// Default UI locale code per top-level region code:
  ///   * JH → `hi` (Hindi)
  ///   * MH → `mr` (Marathi)
  /// Per SDD §5 + §6.3.
  static String defaultLocaleForRegionCode(String regionCode) {
    if (regionCode == regionJH) return 'hi';
    return 'mr';
  }

  /// Brand header shown in the app bar for the given top-level region.
  /// SDD §6.3.
  static String brandForRegionCode(String regionCode) {
    if (regionCode == regionJH) return 'Bagaan Sathi';
    return 'ShetMitra';
  }

  /// Jardalu GI premium multiplier (SDD §3.3). Applied to the current
  /// mandi price when surfacing premium estimates in the farm card.
  static const double jardaluGiPremiumMultiplier = 1.60;

  // ── Variety catalogues ────────────────────────────────────────────
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

  /// Jharkhand mango varieties per SDD §1 / §3.2. Last entry is the
  /// open-ended "Other" so field agents can capture rare local cultivars
  /// without bloating the dropdown.
  static const List<String> jharkhandMangoVarieties = <String>[
    'Mallika',
    'Amrapali',
    'Jardalu',
    'Himsagar',
    'Langra',
    'Other',
  ];

  /// Returns true if `region` is a Jharkhand sub/top-level identifier.
  static bool isJharkhandRegion(String region) {
    final String r = region.toLowerCase();
    return r == 'jh' || r == 'jharkhand';
  }

  /// Returns the variety options to show for [region] + [crop].
  ///
  /// Region rules (SDD §7.2 + §3.2):
  ///   JH / Jharkhand + Mango -> Mallika, Amrapali, Jardalu, Himsagar, Langra, Other
  ///   Konkan + Mango         -> Alphonso, Banganapalli
  ///   Marathwada + Mango     -> Kesar, Totapuri
  ///   Nashik + Mango         -> Kesar, Totapuri
  ///   Vidarbha + Mango       -> Dasheri, Langra
  ///   any other region       -> the full Maharashtra set
  static List<String> varietiesForRegion(String region, String crop) {
    final String c = crop.toLowerCase();
    if (c == 'grapes' || c == 'grape') return _grapeVarieties;
    if (c == 'pomegranate') return _pomegranateVarieties;
    if (c != 'mango') return const <String>[];

    if (isJharkhandRegion(region)) return jharkhandMangoVarieties;

    return switch (region) {
      'Konkan' => const <String>['Alphonso', 'Banganapalli'],
      'Marathwada' => const <String>['Kesar', 'Totapuri'],
      'Nashik' => const <String>['Kesar', 'Totapuri'],
      'Vidarbha' => const <String>['Dasheri', 'Langra'],
      _ => _allMangoVarieties,
    };
  }

  /// Region-aware crop ordering for the registration dropdown — the
  /// crop most relevant to the region is shown first. Jharkhand is a
  /// mango-first belt, so Mango leads.
  static List<String> cropsForRegion(String region) {
    if (isJharkhandRegion(region)) {
      return const <String>['Mango'];
    }
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
