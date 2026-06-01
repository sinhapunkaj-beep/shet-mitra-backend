/// A field agent, owned by Mango Agent 1 in migration 007.
///
/// Territory is region + a list of districts. Each agent only sees
/// farmers whose `district` is in this list (SDD §7.1).
class Agent {
  final String id;
  final String name;
  final String mobile;
  final List<String> districts;
  final String region;
  final bool isActive;

  /// Top-level region code per SDD §2.1 / §6.1:
  ///   * `MH` — Maharashtra (ShetMitra brand)
  ///   * `JH` — Jharkhand (Bagaan Sathi brand)
  /// Defaults to `MH` for legacy rows that pre-date the column.
  final String regionCode;

  const Agent({
    required this.id,
    required this.name,
    required this.mobile,
    required this.districts,
    required this.region,
    required this.isActive,
    this.regionCode = 'MH',
  });

  factory Agent.fromJson(Map<String, dynamic> json) {
    final dynamic rawDistricts = json['districts'];
    final List<String> districts = switch (rawDistricts) {
      List<dynamic> list => list.map((dynamic e) => e.toString()).toList(),
      String s when s.isNotEmpty => s
          .replaceAll(RegExp(r'[{}\[\]"]'), '')
          .split(',')
          .map((String e) => e.trim())
          .where((String e) => e.isNotEmpty)
          .toList(),
      _ => const <String>[],
    };

    return Agent(
      id: (json['id'] ?? '').toString(),
      name: (json['name'] ?? '').toString(),
      mobile: (json['mobile'] ?? '').toString(),
      districts: districts,
      region: (json['region'] ?? '').toString(),
      isActive: json['is_active'] == true || json['is_active'] == 'true',
      regionCode: (json['region_code'] ?? 'MH').toString(),
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'name': name,
        'mobile': mobile,
        'districts': districts,
        'region': region,
        'is_active': isActive,
        'region_code': regionCode,
      };

  Agent copyWith({
    String? id,
    String? name,
    String? mobile,
    List<String>? districts,
    String? region,
    bool? isActive,
    String? regionCode,
  }) =>
      Agent(
        id: id ?? this.id,
        name: name ?? this.name,
        mobile: mobile ?? this.mobile,
        districts: districts ?? this.districts,
        region: region ?? this.region,
        isActive: isActive ?? this.isActive,
        regionCode: regionCode ?? this.regionCode,
      );
}
