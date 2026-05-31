import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../config.dart';
import '../state/auth_state.dart';
import '../utils/i18n.dart';
import '../widgets/language_selector.dart';

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

  @override
  void dispose() {
    _mobileCtrl.dispose();
    _otpCtrl.dispose();
    super.dispose();
  }

  Future<void> _sendOtp() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final AuthState auth = context.read<AuthState>();
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
    return Scaffold(
      appBar: AppBar(
        title: Text(I18n.t('app_title', _locale)),
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
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            const SizedBox(height: 24),
            Text(
              I18n.t('greeting', _locale),
              style: Theme.of(context).textTheme.headlineMedium,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 32),
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
                  'Demo: enter any 10-digit mobile, then OTP 123456 to log in as Tasgaon agent.',
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
