/// Lightweight i18n bag. Three locales per SDD §6.1:
///   * mr  — Marathi (default for inland Maharashtra)
///   * kok — Konkani (auto-suggested when GPS falls inside the Konkan bbox)
///   * en  — English (fallback / urban users)
///
/// We deliberately keep this as plain maps rather than pulling in
/// `flutter_localizations` + ARB files — the agent app only needs a
/// few dozen strings, and shipping a static map keeps the APK small.
class I18n {
  static const String mr = 'mr';
  static const String kok = 'kok';
  static const String en = 'en';

  static const Map<String, Map<String, String>> _strings =
      <String, Map<String, String>>{
    'greeting': <String, String>{
      mr: 'नमस्कार',
      kok: 'नमस्कार', // SDD §6.1 example
      en: 'Hello',
    },
    'app_title': <String, String>{
      mr: 'शेतमित्र एजंट',
      kok: 'शेतमित्र एजेंट',
      en: 'ShetMitra Agent',
    },
    'mobile_label': <String, String>{
      mr: 'मोबाईल नंबर',
      kok: 'मोबाईल क्रमांक',
      en: 'Mobile number',
    },
    'send_otp': <String, String>{
      mr: 'OTP पाठवा',
      kok: 'OTP धाडात',
      en: 'Send OTP',
    },
    'verify_otp': <String, String>{
      mr: 'OTP तपासा',
      kok: 'OTP तपासात',
      en: 'Verify OTP',
    },
    'enter_otp': <String, String>{
      mr: 'OTP टाका',
      kok: 'OTP घालात',
      en: 'Enter OTP',
    },
    'farmers': <String, String>{
      mr: 'शेतकरी',
      kok: 'शेतकार',
      en: 'Farmers',
    },
    'add_farmer': <String, String>{
      mr: 'शेतकरी जोडा',
      kok: 'शेतकार जोडात',
      en: 'Add farmer',
    },
    'belt': <String, String>{
      mr: 'बेल्ट',
      kok: 'पट्टो',
      en: 'Belt',
    },
    'settings': <String, String>{
      mr: 'सेटिंग्ज',
      kok: 'सेटिंग्ज',
      en: 'Settings',
    },
    'full_name': <String, String>{
      mr: 'पूर्ण नाव',
      kok: 'पुराय नांव',
      en: 'Full name',
    },
    'village': <String, String>{
      mr: 'गाव',
      kok: 'गांव',
      en: 'Village',
    },
    'district': <String, String>{
      mr: 'जिल्हा',
      kok: 'जिल्हो',
      en: 'District',
    },
    'crop': <String, String>{
      mr: 'पीक',
      kok: 'पीक',
      en: 'Crop',
    },
    'variety': <String, String>{
      mr: 'जात',
      kok: 'जात',
      en: 'Variety',
    },
    'area_acres': <String, String>{
      mr: 'क्षेत्रफळ (एकर)',
      kok: 'क्षेत्र (एकर)',
      en: 'Area (acres)',
    },
    'tree_count': <String, String>{
      mr: 'झाडांची संख्या',
      kok: 'झाडांची संख्या',
      en: 'Tree count',
    },
    'tree_age': <String, String>{
      mr: 'झाडांचे वय (वर्षे)',
      kok: 'झाडांची उमर (वर्सां)',
      en: 'Tree age (years)',
    },
    'irrigation': <String, String>{
      mr: 'सिंचन प्रकार',
      kok: 'सिंचन प्रकार',
      en: 'Irrigation',
    },
    'bearing_year': <String, String>{
      mr: 'मागील हंगाम',
      kok: 'फाटले हंगाम',
      en: 'Last season bearing',
    },
    'submit': <String, String>{
      mr: 'सबमिट करा',
      kok: 'सबमिट करात',
      en: 'Submit',
    },
    'outside_territory': <String, String>{
      mr: 'तुमच्या कार्यक्षेत्राबाहेर',
      kok: 'तुमच्या कार्यक्षेत्रा भायर',
      en: 'Outside your territory',
    },
    'override_reason': <String, String>{
      mr: 'कारण लिहा',
      kok: 'कारण बरयात',
      en: 'Reason for override',
    },
    'logout': <String, String>{
      mr: 'लॉगआउट',
      kok: 'भायर सरात',
      en: 'Logout',
    },
    'language': <String, String>{
      mr: 'भाषा',
      kok: 'भास',
      en: 'Language',
    },
  };

  /// Returns the localized string for [key] in [locale], falling back
  /// to English, then to the key itself.
  static String t(String key, String locale) {
    final Map<String, String>? row = _strings[key];
    if (row == null) return key;
    return row[locale] ?? row[en] ?? key;
  }

  /// Display name for the locale code.
  static String localeName(String code) => switch (code) {
        mr => 'मराठी',
        kok => 'कोंकणी',
        en => 'English',
        _ => code,
      };

  static const List<String> supportedLocales = <String>[mr, kok, en];
}
