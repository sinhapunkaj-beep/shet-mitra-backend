import 'package:flutter_test/flutter_test.dart';
import 'package:shetmitra_agent/models/agent.dart';

void main() {
  test('Agent.fromJson round-trip', () {
    final Map<String, dynamic> raw = <String, dynamic>{
      'id': 'a-1',
      'name': 'Konkan Demo',
      'mobile': '9876543210',
      'districts': <String>['Ratnagiri', 'Sindhudurg'],
      'region': 'Konkan',
      'is_active': true,
    };
    final Agent a = Agent.fromJson(raw);
    expect(a.id, 'a-1');
    expect(a.name, 'Konkan Demo');
    expect(a.region, 'Konkan');
    expect(a.districts, <String>['Ratnagiri', 'Sindhudurg']);
    expect(a.isActive, isTrue);

    final Map<String, dynamic> back = a.toJson();
    expect(back['id'], 'a-1');
    expect(back['region'], 'Konkan');
    expect((back['districts'] as List<dynamic>).length, 2);
  });

  test('Agent.fromJson tolerates PostgREST text-array districts', () {
    final Agent a = Agent.fromJson(<String, dynamic>{
      'id': 'a-2',
      'name': 'Tasgaon Demo',
      'mobile': '9000000001',
      // Postgres array literal rendered as text
      'districts': '{Sangli,Solapur}',
      'region': 'Tasgaon',
      'is_active': 'true',
    });
    expect(a.districts, <String>['Sangli', 'Solapur']);
    expect(a.isActive, isTrue);
  });
}
