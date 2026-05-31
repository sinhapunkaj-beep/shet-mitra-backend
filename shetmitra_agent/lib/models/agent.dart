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

  const Agent({
    required this.id,
    required this.name,
    required this.mobile,
    required this.districts,
    required this.region,
    required this.isActive,
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
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'name': name,
        'mobile': mobile,
        'districts': districts,
        'region': region,
        'is_active': isActive,
      };

  Agent copyWith({
    String? id,
    String? name,
    String? mobile,
    List<String>? districts,
    String? region,
    bool? isActive,
  }) =>
      Agent(
        id: id ?? this.id,
        name: name ?? this.name,
        mobile: mobile ?? this.mobile,
        districts: districts ?? this.districts,
        region: region ?? this.region,
        isActive: isActive ?? this.isActive,
      );
}
