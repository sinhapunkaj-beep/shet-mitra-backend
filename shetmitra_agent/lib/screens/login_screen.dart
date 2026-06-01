import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:geolocator/geolocator.dart';
import 'package:provider/provider.dart';

import '../config.dart';
import '../state/auth_state.dart';
import '../utils/i18n.dart';
import '../utils/region_helper.dart';
import '../widgets/language_selector.dart';
import '../widgets/region_selector.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController _mobileCtrl = TextEditingController();
  final TextEditingController _otpCtrl = TextEditingController();
  bool _otpSent = false;
  bool _busy = false;
  String? _error;
  String _locale = AppConfig.defaultLocale;
  String _regionCode = RegionHelper.regionMH;
  String? _gpsSuggestion;
  bool _agentTouchedRegion = false;

  @override
  void initState() {
    super.initState();
    // Seed region from any previously-persisted choice, then kick off GPS.
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      final AuthState auth = context.read<AuthState>();
      setState(() {
        _regionCode = auth.activeRegionCode;
        _locale = RegionHelper.defaultLocaleForRegionCode(_regionCode);
      });
      await _autoDetectRegion();
    });
  }

  @override
  void dispose() {
    _mobileCtrl.dispose();
    _otpCtrl.dispose();
    super.dispose();
  }

  Future<void> _autoDetectRegion() async {
    try {
      final bool serviceEnabled = await Geolocator.isLocationServiceEnabled();
      if (!serviceEnabled) return;
      LocationPermission perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.deniedForever ||
          perm == LocationPermission.denied) {
        return;
      }
      final Position pos = await Geolocator.getCurrentPosition(
        desiredAccuracy: LocationAccuracy.medium,
      );
      final String? suggested =
          RegionHelper.suggestTopLevelRegion(pos.latitude, pos.longitude);
      if (!mounted || suggested == null) return;
      setState(() {
        _gpsSuggestion = suggested;
        // Only auto-pick if the agent has not manually changed yet.
        if (!_agentTouchedRegion) {
          _regionCode = suggested;
          _locale = RegionHelper.defaultLocaleForRegionCode(_regionCode);
        }
      });
    } catch (_) {
      // Silent — region remains default and agent can pick manually.
    }
  }

  Future<void> _onRegionChanged(String code) async {
    setState(() {
      _agentTouchedRegion = true;
      _regionCode = code;
      _locale = RegionHelper.defaultLocaleForRegionCode(code);
    });
    await context.read<AuthState>().setRegionCode(code);
  }

  Future<void> _sendOtp() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    // Capture the AuthState ref up front so we don't reach for
    // `context` after an `await` (linter: use_build_context_synchronously).
    final AuthState auth = context.read<AuthState>();
    // Persist the region choice on the auth state so the agent's
    // profile carries it through verifyOtp.
    await auth.setRegionCode(_regionCode);
    final bool ok = await auth.sendOtp(_mobileCtrl.text.trim());
    if (!mounted) return;
    setState(() {
      _busy = false;
      _otpSent = ok;
      if (!ok) _error = 'Invalid mobile number';
    });
  }

  Future<void> _verify() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final AuthState auth = context.read<AuthState>();
    final bool ok = await auth.verifyOtp(_otpCtrl.text.trim());
    if (!mounted) return;
    setState(() => _busy = false);
    if (ok) {
      Navigator.of(context).pushReplacementNamed('/home');
    } else {
      setState(() => _error = 'Invalid OTP — demo OTP is 123456');
    }
  }

  @override
  Widget build(BuildContext context) {
    final String brand = RegionHelper.brandForRegionCode(_regionCode);
    return Scaffold(
      appBar: AppBar(
        title: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(brand, style: const TextStyle(fontWeight: FontWeight.w700)),
            Text(
              I18n.t('app_subtitle', _locale),
              style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w400),
            ),
          ],
        ),
        actions: <Widget>[
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: LanguageSelector(
              value: _locale,
              compact: true,
              onChanged: (String? v) {
                if (v != null) setState(() => _locale = v);
              },
            ),
          ),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            const SizedBox(height: 12),
            Text(
              I18n.t('greeting', _locale),
              style: Theme.of(context).textTheme.headlineMedium,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 24),
            RegionSelector(
              value: _regionCode,
              gpsSuggestion: _gpsSuggestion,
              locale: _locale,
              onChanged: _onRegionChanged,
            ),
            const SizedBox(height: 24),
            TextField(
              controller: _mobileCtrl,
              keyboardType: TextInputType.phone,
              inputFormatters: <TextInputFormatter>[
                FilteringTextInputFormatter.digitsOnly,
                LengthLimitingTextInputFormatter(10),
              ],
              decoration: InputDecoration(
                labelText: '${I18n.t('mobile_label', _locale)} / Mobile',
                border: const OutlineInputBorder(),
                prefixText: '+91 ',
              ),
            ),
            const SizedBox(height: 16),
            if (!_otpSent)
              FilledButton(
                onPressed: _busy ? null : _sendOtp,
                child: Text(I18n.t('send_otp', _locale)),
              )
            else ...<Widget>[
              TextField(
                controller: _otpCtrl,
                keyboardType: TextInputType.number,
                inputFormatters: <TextInputFormatter>[
                  FilteringTextInputFormatter.digitsOnly,
                  LengthLimitingTextInputFormatter(6),
                ],
                decoration: InputDecoration(
                  labelText: I18n.t('enter_otp', _locale),
                  border: const OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: _busy ? null : _verify,
                child: Text(I18n.t('verify_otp', _locale)),
              ),
            ],
            if (_error != null) ...<Widget>[
              const SizedBox(height: 12),
              Text(_error!, style: const TextStyle(color: Colors.red)),
            ],
            const SizedBox(height: 24),
            const Card(
              child: Padding(
                padding: EdgeInsets.all(12),
                child: Text(
                  'Demo: enter any 10-digit mobile, then OTP 123456. '
                  'Pick MH for ShetMitra (Marathi) or JH for Bagaan Sathi (Hindi).',
                  style: TextStyle(fontStyle: FontStyle.italic, fontSize: 12),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
