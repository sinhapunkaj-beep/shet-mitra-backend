/// A plot (farm parcel) belonging to a farmer.
///
/// Mango-specific fields (treeCount, treeAgeYears, bearingYear,
/// flowering / fruitSet flags) are populated by the AMED pipeline and
/// the field agent during registration.
class Plot {
  final String id;
  final String farmerId;
  final double areaAcres;
  final String? currentCropVariety;
  final int? treeCount;
  final int? treeAgeYears;
  final String? bearingYear; // 'ON' | 'OFF' | 'Unknown'
  final bool floweringDetected;
  final bool fruitSetDetected;
  final String? cropRegion; // 'Konkan' | 'Nashik' | 'Marathwada' | 'Vidarbha' | 'Tasgaon'

  const Plot({
    required this.id,
    required this.farmerId,
    required this.areaAcres,
    this.currentCropVariety,
    this.treeCount,
    this.treeAgeYears,
    this.bearingYear,
    this.floweringDetected = false,
    this.fruitSetDetected = false,
    this.cropRegion,
  });

  factory Plot.fromJson(Map<String, dynamic> json) => Plot(
        id: (json['id'] ?? '').toString(),
        farmerId: (json['farmer_id'] ?? '').toString(),
        areaAcres: _toDouble(json['area_acres']) ?? 0.0,
        currentCropVariety: json['current_crop_variety']?.toString(),
        treeCount: _toInt(json['tree_count']),
        treeAgeYears: _toInt(json['tree_age_years']),
        bearingYear: json['bearing_year']?.toString(),
        floweringDetected: json['flowering_detected'] == true,
        fruitSetDetected: json['fruit_set_detected'] == true,
        cropRegion: json['crop_region']?.toString(),
      );

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'farmer_id': farmerId,
        'area_acres': areaAcres,
        'current_crop_variety': currentCropVariety,
        'tree_count': treeCount,
        'tree_age_years': treeAgeYears,
        'bearing_year': bearingYear,
        'flowering_detected': floweringDetected,
        'fruit_set_detected': fruitSetDetected,
        'crop_region': cropRegion,
      };

  static double? _toDouble(dynamic v) {
    if (v == null) return null;
    if (v is num) return v.toDouble();
    return double.tryParse(v.toString());
  }

  static int? _toInt(dynamic v) {
    if (v == null) return null;
    if (v is int) return v;
    if (v is num) return v.toInt();
    return int.tryParse(v.toString());
  }
}
