import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/agent.dart';

/// Auth state for the agent app.
///
/// The OTP flow is intentionally stubbed in this scaffold — `sendOtp`
/// returns true for any 10-digit mobile, and `verifyOtp` accepts the
/// SDD demo OTP `123456`. Replace with Supabase phone auth (or the
/// Twilio bridge configured by Mango Agent 4) before release.
class AuthState extends ChangeNotifier {
  Agent? _currentAgent;
  String? _pendingMobile;
  bool _bootstrapped = false;

  Agent? get currentAgent => _currentAgent;
  bool get isLoggedIn => _currentAgent != null;
  bool get bootstrapped => _bootstrapped;

  static const String _prefsMobileKey = 'shetmitra.agent.mobile';

  /// Restore the previously-logged-in agent (if any) from
  /// SharedPreferences. Called from `main.dart` on startup.
  Future<void> bootstrap() async {
    try {
      final SharedPreferences prefs = await SharedPreferences.getInstance();
      final String? mobile = prefs.getString(_prefsMobileKey);
      if (mobile != null && mobile.isNotEmpty) {
        _currentAgent = _demoAgentFor(mobile);
      }
    } catch (_) {
      // SharedPreferences is unavailable in unit tests without binding —
      // swallow and continue.
    } finally {
      _bootstrapped = true;
      notifyListeners();
    }
  }

  /// Send an OTP to [mobile]. Returns true if the mobile looks valid.
  ///
  /// In the live build this would POST to the OTP gateway (Supabase
  /// phone auth / Twilio). Here it just records the pending mobile.
  Future<bool> sendOtp(String mobile) async {
    final String cleaned = mobile.replaceAll(RegExp(r'\D'), '');
    if (cleaned.length != 10) return false;
    _pendingMobile = cleaned;
    // Simulate network latency so the UI spinner is realistic.
    await Future<void>.delayed(const Duration(milliseconds: 400));
    return true;
  }

  /// Verify the OTP. Demo OTP per SDD §7 is `123456`.
  Future<bool> verifyOtp(String otp) async {
    if (_pendingMobile == null) return false;
    final String cleaned = otp.replaceAll(RegExp(r'\D'), '');
    if (cleaned.length != 6) return false;
    if (cleaned != '123456') return false;

    _currentAgent = _demoAgentFor(_pendingMobile!);

    try {
      final SharedPreferences prefs = await SharedPreferences.getInstance();
      await prefs.setString(_prefsMobileKey, _pendingMobile!);
    } catch (_) {
      // ignore in unit-test contexts
    }

    notifyListeners();
    return true;
  }

  Future<void> logout() async {
    _currentAgent = null;
    _pendingMobile = null;
    try {
      final SharedPreferences prefs = await SharedPreferences.getInstance();
      await prefs.remove(_prefsMobileKey);
    } catch (_) {}
    notifyListeners();
  }

  /// Maps the last digit of the mobile to one of the 4 seeded agents
  /// (Tasgaon / Konkan / Nashik / Vidarbha). Default = Tasgaon, so the
  /// SDD demo phrase "enter 123456 to log in as Tasgaon agent" works
  /// with any 10-digit number.
  Agent _demoAgentFor(String mobile) {
    final int suffix = int.tryParse(mobile.substring(mobile.length - 1)) ?? 0;
    return switch (suffix % 4) {
      1 => const Agent(
          id: 'demo-konkan',
          name: 'Konkan Agent',
          mobile: '',
          districts: <String>['Ratnagiri', 'Sindhudurg', 'Raigad'],
          region: 'Konkan',
          isActive: true,
        ),
      2 => const Agent(
          id: 'demo-nashik',
          name: 'Nashik Agent',
          mobile: '',
          districts: <String>['Nashik', 'Ahmednagar'],
          region: 'Nashik',
          isActive: true,
        ),
      3 => const Agent(
          id: 'demo-vidarbha',
          name: 'Vidarbha Agent',
          mobile: '',
          districts: <String>['Amravati', 'Nagpur', 'Yavatmal'],
          region: 'Vidarbha',
          isActive: true,
        ),
      _ => const Agent(
          id: 'demo-tasgaon',
          name: 'Tasgaon Agent',
          mobile: '',
          districts: <String>['Sangli', 'Solapur'],
          region: 'Tasgaon',
          isActive: true,
        ),
    }
        .copyWith(mobile: mobile);
  }
}
