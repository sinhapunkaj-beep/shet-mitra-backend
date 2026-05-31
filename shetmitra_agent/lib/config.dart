/// App-wide configuration constants.
///
/// In production these should be injected at build-time via
/// `--dart-define` (or an env file consumed by `flutter_dotenv`) so that
/// the keys are not committed in source. The anon key embedded here is
/// the public published anon key used across the ShetMitra swarm — it
/// is safe to ship in the app because it only grants RLS-protected
/// access — but real per-tenant secrets must never live in this file.
class AppConfig {
  static const String supabaseUrl =
      'https://euydubpywdsettjywkms.supabase.co';

  static const String supabaseAnonKey =
      'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.'
      'eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV1eWR1YnB5d2RzZXR0anl3a21zIiwicm9sZSI6ImFub24ifQ.'
      'PUBLISHED_ANON_KEY_PLACEHOLDER';

  /// Default UI language code on first launch. Marathi per SDD §6.1.
  static const String defaultLocale = 'mr';

  /// REST path prefix exposed by PostgREST behind Supabase.
  static const String restPrefix = '/rest/v1';
}
