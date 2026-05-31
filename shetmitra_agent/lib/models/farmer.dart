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
      };

  static double? _toDouble(dynamic v) {
    if (v == null) return null;
    if (v is num) return v.toDouble();
    return double.tryParse(v.toString());
  }
}
