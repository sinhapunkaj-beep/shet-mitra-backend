/// Lightweight i18n bag. Four locales per SDD §5 + §6.1:
///   * mr  — Marathi (default for inland Maharashtra)
///   * kok — Konkani (auto-suggested when GPS falls inside the Konkan bbox)
///   * hi  — Hindi (default for Jharkhand / Bagaan Sathi region)
///   * en  — English (fallback / urban users)
///
/// We deliberately keep this as plain maps rather than pulling in
/// `flutter_localizations` + ARB files — the agent app only needs a
/// few dozen strings, and shipping a static map keeps the APK small.
class I18n {
  static const String mr = 'mr';
  static const String kok = 'kok';
  static const String hi = 'hi';
  static const String en = 'en';

  static const Map<String, Map<String, String>> _strings =
      <String, Map<String, String>>{
    'greeting': <String, String>{
      mr: 'नमस्कार',
      kok: 'नमस्कार', // SDD §6.1 example
      hi: 'नमस्ते',
      en: 'Hello',
    },
    'app_title': <String, String>{
      mr: 'शेतमित्र एजंट',
      kok: 'शेतमित्र एजेंट',
      hi: 'बागान साथी',
      en: 'ShetMitra Agent',
    },
    'app_subtitle': <String, String>{
      mr: 'Sahyadri Krushi Intelligence',
      kok: 'Sahyadri Krushi Intelligence',
      hi: 'Sahyadri Krushi Intelligence',
      en: 'Sahyadri Krushi Intelligence',
    },
    'mobile_label': <String, String>{
      mr: 'मोबाईल नंबर',
      kok: 'मोबाईल क्रमांक',
      hi: 'मोबाइल नंबर',
      en: 'Mobile number',
    },
    'send_otp': <String, String>{
      mr: 'OTP पाठवा',
      kok: 'OTP धाडात',
      hi: 'OTP भेजें',
      en: 'Send OTP',
    },
    'verify_otp': <String, String>{
      mr: 'OTP तपासा',
      kok: 'OTP तपासात',
      hi: 'OTP जांचें',
      en: 'Verify OTP',
    },
    'enter_otp': <String, String>{
      mr: 'OTP टाका',
      kok: 'OTP घालात',
      hi: 'OTP दर्ज करें',
      en: 'Enter OTP',
    },
    'farmers': <String, String>{
      mr: 'शेतकरी',
      kok: 'शेतकार',
      hi: 'किसान',
      en: 'Farmers',
    },
    'add_farmer': <String, String>{
      mr: 'शेतकरी जोडा',
      kok: 'शेतकार जोडात',
      hi: 'किसान जोड़ें',
      en: 'Add farmer',
    },
    'belt': <String, String>{
      mr: 'बेल्ट',
      kok: 'पट्टो',
      hi: 'बेल्ट',
      en: 'Belt',
    },
    'settings': <String, String>{
      mr: 'सेटिंग्ज',
      kok: 'सेटिंग्ज',
      hi: 'सेटिंग्स',
      en: 'Settings',
    },
    'full_name': <String, String>{
      mr: 'पूर्ण नाव',
      kok: 'पुराय नांव',
      hi: 'पूरा नाम',
      en: 'Full name',
    },
    'village': <String, String>{
      mr: 'गाव',
      kok: 'गांव',
      hi: 'गाँव',
      en: 'Village',
    },
    'district': <String, String>{
      mr: 'जिल्हा',
      kok: 'जिल्हो',
      hi: 'जिला',
      en: 'District',
    },
    'crop': <String, String>{
      mr: 'पीक',
      kok: 'पीक',
      hi: 'फसल',
      en: 'Crop',
    },
    'variety': <String, String>{
      mr: 'जात',
      kok: 'जात',
      hi: 'किस्म',
      en: 'Variety',
    },
    'area_acres': <String, String>{
      mr: 'क्षेत्रफळ (एकर)',
      kok: 'क्षेत्र (एकर)',
      hi: 'क्षेत्रफल (एकड़)',
      en: 'Area (acres)',
    },
    'tree_count': <String, String>{
      mr: 'झाडांची संख्या',
      kok: 'झाडांची संख्या',
      hi: 'पेड़ों की संख्या',
      en: 'Tree count',
    },
    'tree_age': <String, String>{
      mr: 'झाडांचे वय (वर्षे)',
      kok: 'झाडांची उमर (वर्सां)',
      hi: 'पेड़ों की उम्र (वर्ष)',
      en: 'Tree age (years)',
    },
    'irrigation': <String, String>{
      mr: 'सिंचन प्रकार',
      kok: 'सिंचन प्रकार',
      hi: 'सिंचाई का प्रकार',
      en: 'Irrigation',
    },
    'bearing_year': <String, String>{
      mr: 'मागील हंगाम',
      kok: 'फाटले हंगाम',
      hi: 'पिछला सीजन',
      en: 'Last season bearing',
    },
    'last_yield_kg_per_tree': <String, String>{
      mr: 'मागील हंगाम उत्पादन (किग्रा/झाड)',
      kok: 'फाटले हंगाम उत्पादन (किग्रा/झाड)',
      hi: 'पिछला उत्पादन (किग्रा/पेड़)',
      en: 'Last yield (kg/tree)',
    },
    'submit': <String, String>{
      mr: 'सबमिट करा',
      kok: 'सबमिट करात',
      hi: 'जमा करें',
      en: 'Submit',
    },
    'outside_territory': <String, String>{
      mr: 'तुमच्या कार्यक्षेत्राबाहेर',
      kok: 'तुमच्या कार्यक्षेत्रा भायर',
      hi: 'आपके क्षेत्र के बाहर',
      en: 'Outside your territory',
    },
    'override_reason': <String, String>{
      mr: 'कारण लिहा',
      kok: 'कारण बरयात',
      hi: 'कारण लिखें',
      en: 'Reason for override',
    },
    'logout': <String, String>{
      mr: 'लॉगआउट',
      kok: 'भायर सरात',
      hi: 'लॉगआउट',
      en: 'Logout',
    },
    'language': <String, String>{
      mr: 'भाषा',
      kok: 'भास',
      hi: 'भाषा',
      en: 'Language',
    },
    'region': <String, String>{
      mr: 'प्रदेश',
      kok: 'प्रदेश',
      hi: 'क्षेत्र',
      en: 'Region',
    },
    'select_region': <String, String>{
      mr: 'प्रदेश निवडा',
      kok: 'प्रदेश निवडात',
      hi: 'क्षेत्र चुनें',
      en: 'Select region',
    },
    'region_mh': <String, String>{
      mr: 'महाराष्ट्र (ShetMitra)',
      kok: 'महाराष्ट्र (ShetMitra)',
      hi: 'महाराष्ट्र (ShetMitra)',
      en: 'Maharashtra (ShetMitra)',
    },
    'region_jh': <String, String>{
      mr: 'झारखंड (Bagaan Sathi)',
      kok: 'झारखंड (Bagaan Sathi)',
      hi: 'झारखंड (Bagaan Sathi)',
      en: 'Jharkhand (Bagaan Sathi)',
    },
    'gps_suggesting_region': <String, String>{
      mr: 'GPS नुसार सुचवलेला प्रदेश',
      kok: 'GPS नुसार सुचयलो प्रदेश',
      hi: 'GPS के अनुसार सुझाया क्षेत्र',
      en: 'GPS-suggested region',
    },
    'gi_zone_badge': <String, String>{
      mr: 'GI Zone ✅',
      kok: 'GI Zone ✅',
      hi: 'GI Zone ✅',
      en: 'GI Zone ✅',
    },
    'jardalu_gi_badge': <String, String>{
      mr: 'Jardalu GI ✅',
      kok: 'Jardalu GI ✅',
      hi: 'Jardalu GI ✅',
      en: 'Jardalu GI ✅',
    },
    'gi_premium_estimate': <String, String>{
      mr: 'अंदाजे GI प्रीमियम',
      kok: 'अंदाजे GI प्रीमियम',
      hi: 'अनुमानित GI प्रीमियम',
      en: 'Est. GI premium',
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
        hi => 'हिन्दी',
        en => 'English',
        _ => code,
      };

  static const List<String> supportedLocales = <String>[mr, kok, hi, en];
}
