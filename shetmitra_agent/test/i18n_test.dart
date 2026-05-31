import 'package:flutter_test/flutter_test.dart';
import 'package:shetmitra_agent/utils/i18n.dart';

void main() {
  test('Konkani greeting matches SDD §6.1 example', () {
    // SDD §6.1 lists "नमस्कार" as the Konkani greeting.
    expect(I18n.t('greeting', I18n.kok), 'नमस्कार');
  });

  test('falls back to English for unknown locale', () {
    expect(I18n.t('send_otp', 'fr'), 'Send OTP');
  });

  test('returns the key itself when string is missing', () {
    expect(I18n.t('no_such_key', I18n.en), 'no_such_key');
  });

  test('all three supported locales are present', () {
    expect(I18n.supportedLocales, containsAll(<String>['mr', 'kok', 'en']));
  });
}
