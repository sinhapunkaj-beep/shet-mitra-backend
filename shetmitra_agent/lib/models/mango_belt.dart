/// One row of the `mango_belt_data` aggregate, populated by the AMED
/// pipeline. The Belt Intelligence screen renders one card per row.
class MangoBelt {
  final String region;
  final String variety;
  final int totalFields;
  final double totalAreaAcres;
  final double estimatedVolumeMt;
  final String? bearingYear; // 'ON' | 'OFF' | 'Unknown'

  const MangoBelt({
    required this.region,
    required this.variety,
    required this.totalFields,
    required this.totalAreaAcres,
    required this.estimatedVolumeMt,
    this.bearingYear,
  });

  factory MangoBelt.fromJson(Map<String, dynamic> json) => MangoBelt(
        region: (json['region'] ?? '').toString(),
        variety: (json['variety'] ?? '').toString(),
        totalFields: _toInt(json['total_fields']) ?? 0,
        totalAreaAcres: _toDouble(json['total_area_acres']) ?? 0.0,
        estimatedVolumeMt: _toDouble(json['estimated_volume_mt']) ?? 0.0,
        bearingYear: json['bearing_year']?.toString(),
      );

  Map<String, dynamic> toJson() => <String, dynamic>{
        'region': region,
        'variety': variety,
        'total_fields': totalFields,
        'total_area_acres': totalAreaAcres,
        'estimated_volume_mt': estimatedVolumeMt,
        'bearing_year': bearingYear,
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
