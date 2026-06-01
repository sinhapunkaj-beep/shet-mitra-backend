/// A farmer record. Mirrors the `farmers` table that Mango Agent 1
/// extends in migration 007 (AMED detection + variety capture columns).
class Farmer {
  final String id;
  final String fullName;
  final String mobile;
  final String village;
  final String district;
  final String currentCrop;
  final String? currentCropVariety;
  final double areaAcres;
  final double? centroidLat;
  final double? centroidLng;
  final String? amedCropDetected;
  final bool amedVarietyCollected;
  final String regionCode;

  /// True when the GI verifier has confirmed this farmer's primary
  /// plot qualifies for the Jardalu GI premium (SDD §3.3). Populated
  /// by the backend via `farm_plots.gi_verified` after the AMED
  /// pipeline runs.
  final bool giVerified;

  /// Latest mandi reference price (Rs/kg) for this farmer's crop.
  /// Surfaced so the farm card can show the projected GI premium
  /// without an extra backend call.
  final double? referenceMandiPrice;

  const Farmer({
    required this.id,
    required this.fullName,
    required this.mobile,
    required this.village,
    required this.district,
    required this.currentCrop,
    required this.areaAcres,
    this.currentCropVariety,
    this.centroidLat,
    this.centroidLng,
    this.amedCropDetected,
    this.amedVarietyCollected = false,
    this.regionCode = 'MH',
    this.giVerified = false,
    this.referenceMandiPrice,
  });

  factory Farmer.fromJson(Map<String, dynamic> json) => Farmer(
        id: (json['id'] ?? '').toString(),
        fullName: (json['full_name'] ?? json['name'] ?? '').toString(),
        mobile: (json['mobile'] ?? '').toString(),
        village: (json['village'] ?? '').toString(),
        district: (json['district'] ?? '').toString(),
        currentCrop: (json['current_crop'] ?? '').toString(),
        currentCropVariety: json['current_crop_variety']?.toString(),
        areaAcres: _toDouble(json['area_acres']) ?? 0.0,
        centroidLat: _toDouble(json['centroid_lat']),
        centroidLng: _toDouble(json['centroid_lng']),
        amedCropDetected: json['amed_crop_detected']?.toString(),
        amedVarietyCollected:
            json['amed_variety_collected'] == true ||
                json['amed_variety_collected'] == 'true',
        regionCode: (json['region_code'] ?? 'MH').toString(),
        giVerified: json['gi_verified'] == true ||
            json['gi_verified'] == 'true',
        referenceMandiPrice:
            _toDouble(json['reference_mandi_price']),
      );

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'full_name': fullName,
        'mobile': mobile,
        'village': village,
        'district': district,
        'current_crop': currentCrop,
        'current_crop_variety': currentCropVariety,
        'area_acres': areaAcres,
        'centroid_lat': centroidLat,
        'centroid_lng': centroidLng,
        'amed_crop_detected': amedCropDetected,
        'amed_variety_collected': amedVarietyCollected,
        'region_code': regionCode,
        'gi_verified': giVerified,
        'reference_mandi_price': referenceMandiPrice,
      };

  static double? _toDouble(dynamic v) {
    if (v == null) return null;
    if (v is num) return v.toDouble();
    return double.tryParse(v.toString());
  }
}
