import 'package:flutter/foundation.dart';

import '../models/agent.dart';
import '../models/farmer.dart';
import '../models/mango_belt.dart';
import '../utils/supabase_client.dart';

/// Holds the data shown on the home shell (farmers list, belt cards)
/// for the currently-logged-in agent.
class AgentState extends ChangeNotifier {
  AgentState({SupabaseClient? client}) : _client = client ?? SupabaseClient();

  final SupabaseClient _client;

  List<Farmer> _farmersInTerritory = <Farmer>[];
  List<MangoBelt> _mangoBelt = <MangoBelt>[];
  bool _loadingFarmers = false;
  String? _lastError;

  List<Farmer> get farmersInTerritory => List<Farmer>.unmodifiable(_farmersInTerritory);
  List<MangoBelt> get mangoBelt => List<MangoBelt>.unmodifiable(_mangoBelt);
  bool get loadingFarmers => _loadingFarmers;
  String? get lastError => _lastError;

  /// Loads all farmers whose `district` is one of [agent.districts].
  /// Uses PostgREST's `in.(...)` filter.
  Future<void> loadFarmers(Agent agent) async {
    _loadingFarmers = true;
    _lastError = null;
    notifyListeners();
    try {
      if (agent.districts.isEmpty) {
        _farmersInTerritory = <Farmer>[];
      } else {
        final String inList = agent.districts.map((String d) => '"$d"').join(',');
        final List<dynamic> rows = await _client.get(
          '/farmers',
          params: <String, String>{
            'district': 'in.($inList)',
            'select': '*',
            'order': 'created_at.desc',
            'limit': '500',
          },
        );
        _farmersInTerritory = rows
            .whereType<Map<String, dynamic>>()
            .map<Farmer>((Map<String, dynamic> r) => Farmer.fromJson(r))
            .toList();
      }
    } catch (e) {
      _lastError = e.toString();
      // Keep the previous list visible; do not blank the UI on error.
    } finally {
      _loadingFarmers = false;
      notifyListeners();
    }
  }

  /// Loads `mango_belt_data` rows scoped to [agent.region]. The screen
  /// renders a tab per region in the agent's territory; right now each
  /// agent owns a single region so we pull just that one.
  Future<void> loadMangoBelt(Agent agent) async {
    try {
      final List<dynamic> rows = await _client.get(
        '/mango_belt_data',
        params: <String, String>{
          'region': 'eq.${agent.region}',
          'select': '*',
          'order': 'total_area_acres.desc',
        },
      );
      _mangoBelt = rows
          .whereType<Map<String, dynamic>>()
          .map<MangoBelt>((Map<String, dynamic> r) => MangoBelt.fromJson(r))
          .toList();
    } catch (e) {
      _lastError = e.toString();
    } finally {
      notifyListeners();
    }
  }

  /// Register a farmer + its primary plot.
  ///
  /// Returns the newly-created farmer id on success, or null on
  /// failure. The caller should refresh `loadFarmers` afterwards.
  Future<String?> registerFarmer({
    required String fullName,
    required String mobile,
    required String village,
    required String district,
    required String currentCrop,
    required double areaAcres,
    String? currentCropVariety,
    double? centroidLat,
    double? centroidLng,
    int? treeCount,
    int? treeAgeYears,
    String? bearingYear,
    String? irrigationType,
    String? cropRegion,
    String? regionCode,
    double? lastYieldKgPerTree,
    bool giVerified = false,
    bool overrideTerritory = false,
    String? overrideReason,
  }) async {
    try {
      final Map<String, dynamic> farmerBody = <String, dynamic>{
        'full_name': fullName,
        'mobile': mobile,
        'village': village,
        'district': district,
        'current_crop': currentCrop,
        'current_crop_variety': currentCropVariety,
        'area_acres': areaAcres,
        'centroid_lat': centroidLat,
        'centroid_lng': centroidLng,
        if (regionCode != null && regionCode.isNotEmpty)
          'region_code': regionCode,
        if (overrideTerritory)
          'territory_override_reason': overrideReason ?? 'unspecified',
      };

      final dynamic farmerResp = await _client.post('/farmers', farmerBody);
      final String? newId = _extractFirstId(farmerResp);
      if (newId == null) return null;

      // Insert the primary plot. For non-mango crops the mango columns
      // are simply null.
      final Map<String, dynamic> plotBody = <String, dynamic>{
        'farmer_id': newId,
        'area_acres': areaAcres,
        'current_crop_variety': currentCropVariety,
        'tree_count': treeCount,
        'tree_age_years': treeAgeYears,
        'bearing_year': bearingYear,
        'irrigation_type': irrigationType,
        'crop_region': cropRegion,
        if (regionCode != null && regionCode.isNotEmpty)
          'region_code': regionCode,
        if (lastYieldKgPerTree != null)
          'last_season_yield_kg_per_tree': lastYieldKgPerTree,
        if (giVerified) 'gi_verified': true,
      };
      await _client.post('/farm_plots', plotBody);

      return newId;
    } catch (e) {
      _lastError = e.toString();
      notifyListeners();
      return null;
    }
  }

  String? _extractFirstId(dynamic resp) {
    if (resp is List && resp.isNotEmpty) {
      final dynamic row = resp.first;
      if (row is Map && row['id'] != null) return row['id'].toString();
    }
    if (resp is Map && resp['id'] != null) return resp['id'].toString();
    return null;
  }
}
