# shetmitra_agent

Flutter companion app for ShetMitra field agents. Implements the
region-based territory model described in SDD §7: each agent owns a list
of districts inside a region (Tasgaon / Konkan / Nashik / Vidarbha),
registers farmers + plots inside that territory, and views mango-belt
intelligence cards limited to their region.

## What is in this scaffold

- `lib/main.dart` — app entry, providers, routing
- `lib/config.dart` — Supabase URL + published anon key
- `lib/models/` — `Agent`, `Farmer`, `Plot`, `MangoBelt`
- `lib/state/` — `AuthState` (OTP stub) and `AgentState`
- `lib/screens/` — login, home, farmers list, register farmer,
  belt intelligence, settings
- `lib/widgets/` — region-aware variety dropdown, language selector,
  crop chip
- `lib/utils/` — `SupabaseClient` http wrapper, region helper,
  i18n strings (mr / kok / en)
- `test/` — light unit tests that document expected behaviour

## Build & run

This repo only contains the Dart source. On a workstation with the
Flutter 3.16+ SDK and an Android SDK installed:

```bash
cd shetmitra_agent
flutter pub get
flutter run            # debug
flutter build apk      # release artifact
flutter test           # run the unit tests under test/
```

## Stubs / follow-ups

- OTP delivery in `AuthState.sendOtp` is stubbed — it accepts any
  10-digit mobile and treats `123456` as a valid OTP. Wire to Supabase
  phone auth (or the existing twilio bridge) before release.
- `supabase_flutter` is intentionally not added yet. The scaffold uses a
  thin `http` wrapper against the REST endpoint with the published anon
  key. Swap to `supabase_flutter` when realtime / RLS-protected writes
  are needed.
- District auto-detection uses `geolocator` + a placeholder reverse
  geocode. Replace `GeoService.reverseDistrict` with a real lookup
  (Mapbox / OSM Nominatim / the in-repo geo service) for production.
- `flutter_local_notifications` is wired in `pubspec.yaml` only — the
  outbound notification flow is left for the alerts swarm.
