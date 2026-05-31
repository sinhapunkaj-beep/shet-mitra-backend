import 'package:flutter_test/flutter_test.dart';
import 'package:shetmitra_agent/utils/region_helper.dart';

void main() {
  group('RegionHelper.isInKonkanBbox', () {
    test('point inside the Konkan coastal bbox', () {
      expect(RegionHelper.isInKonkanBbox(17.0, 73.3), isTrue);
    });
    test('point inland is not Konkan', () {
      // Tasgaon area
      expect(RegionHelper.isInKonkanBbox(16.9, 74.6), isFalse);
    });
    test('point too far north is not Konkan', () {
      expect(RegionHelper.isInKonkanBbox(20.0, 73.3), isFalse);
    });
  });

  group('RegionHelper.varietiesForRegion (Mango)', () {
    test('Konkan includes Alphonso', () {
      final List<String> v = RegionHelper.varietiesForRegion('Konkan', 'Mango');
      expect(v.contains('Alphonso'), isTrue);
      expect(v.contains('Banganapalli'), isTrue);
      expect(v.length, 2);
    });
    test('Nashik includes Kesar but not Alphonso', () {
      final List<String> v = RegionHelper.varietiesForRegion('Nashik', 'Mango');
      expect(v.contains('Kesar'), isTrue);
      expect(v.contains('Alphonso'), isFalse);
    });
    test('Vidarbha includes Dasheri and Langra', () {
      final List<String> v =
          RegionHelper.varietiesForRegion('Vidarbha', 'Mango');
      expect(v, containsAll(<String>['Dasheri', 'Langra']));
    });
    test('Unknown region returns full mango list', () {
      final List<String> v =
          RegionHelper.varietiesForRegion('Tasgaon', 'Mango');
      expect(v.length, greaterThanOrEqualTo(5));
    });
  });
}
