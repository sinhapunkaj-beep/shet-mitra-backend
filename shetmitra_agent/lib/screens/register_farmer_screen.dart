import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:geolocator/geolocator.dart';
import 'package:provider/provider.dart';

import '../models/agent.dart';
import '../state/agent_state.dart';
import '../state/auth_state.dart';
import '../utils/i18n.dart';
import '../utils/region_helper.dart';
import '../widgets/language_selector.dart';
import '../widgets/variety_dropdown.dart';

class RegisterFarmerScreen extends StatefulWidget {
  const RegisterFarmerScreen({super.key, required this.locale});

  final String locale;

  @override
  State<RegisterFarmerScreen> createState() => _RegisterFarmerScreenState();
}

class _RegisterFarmerScreenState extends State<RegisterFarmerScreen> {
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  final TextEditingController _nameCtrl = TextEditingController();
  final TextEditingController _mobileCtrl = TextEditingController();
  final TextEditingController _villageCtrl = TextEditingController();
  final TextEditingController _districtCtrl = TextEditingController();
  final TextEditingController _areaCtrl = TextEditingController();
  final TextEditingController _treeCountCtrl = TextEditingController();
  final TextEditingController _treeAgeCtrl = TextEditingController();
  final TextEditingController _lastYieldCtrl = TextEditingController();
  final TextEditingController _overrideReasonCtrl = TextEditingController();

  String? _selectedCrop;
  String? _selectedVariety;
  String _irrigation = 'Drip';
  String _bearing = 'Unknown';
  double? _lat;
  double? _lng;
  bool _outsideTerritory = false;
  bool _overrideAccepted = false;
  bool _busy = false;
  String? _msg;
  String? _localLocale;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _detectLocation());
  }

  String get _locale => _localLocale ?? widget.locale;

  @override
  void dispose() {
    _nameCtrl.dispose();
    _mobileCtrl.dispose();
    _villageCtrl.dispose();
    _districtCtrl.dispose();
    _areaCtrl.dispose();
    _treeCountCtrl.dispose();
    _treeAgeCtrl.dispose();
    _lastYieldCtrl.dispose();
    _overrideReasonCtrl.dispose();
    super.dispose();
  }

  Future<void> _detectLocation() async {
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
        desiredAccuracy: LocationAccuracy.high,
      );
      if (!mounted) return;
      setState(() {
        _lat = pos.latitude;
        _lng = pos.longitude;
        // Auto-suggest Konkani if the agent is inside the Konkan bbox.
        if (_localLocale == null &&
            RegionHelper.isInKonkanBbox(pos.latitude, pos.longitude)) {
          _localLocale = I18n.kok;
        }
      });
      _evaluateTerritory();
    } catch (_) {
      // Silently ignore — agent can type the district manually.
    }
  }

  void _evaluateTerritory() {
    final Agent? agent = context.read<AuthState>().currentAgent;
    if (agent == null) return;
    final String typed = _districtCtrl.text.trim();
    if (typed.isEmpty) {
      setState(() => _outsideTerritory = false);
      return;
    }
    final bool inTerritory =
        agent.districts.any((String d) => d.toLowerCase() == typed.toLowerCase());
    setState(() => _outsideTerritory = !inTerritory);
  }

  bool get _isJardaluGiEligible {
    if (_selectedVariety == null) return false;
    return RegionHelper.isJardaluGiEligible(
      variety: _selectedVariety!,
      lat: _lat,
      lng: _lng,
    );
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    if (_outsideTerritory && !_overrideAccepted) {
      setState(() => _msg = I18n.t('outside_territory', _locale));
      return;
    }

    final Agent? agent = context.read<AuthState>().currentAgent;
    if (agent == null) return;

    setState(() {
      _busy = true;
      _msg = null;
    });

    final String? newId = await context.read<AgentState>().registerFarmer(
          fullName: _nameCtrl.text.trim(),
          mobile: _mobileCtrl.text.trim(),
          village: _villageCtrl.text.trim(),
          district: _districtCtrl.text.trim(),
          currentCrop: _selectedCrop ?? '',
          currentCropVariety: _selectedVariety,
          areaAcres: double.tryParse(_areaCtrl.text.trim()) ?? 0.0,
          centroidLat: _lat,
          centroidLng: _lng,
          treeCount: int.tryParse(_treeCountCtrl.text.trim()),
          treeAgeYears: int.tryParse(_treeAgeCtrl.text.trim()),
          bearingYear: _selectedCrop == 'Mango' ? _bearing : null,
          irrigationType: _selectedCrop == 'Mango' ? _irrigation : null,
          cropRegion: agent.region,
          regionCode: agent.regionCode,
          lastYieldKgPerTree:
              double.tryParse(_lastYieldCtrl.text.trim()),
          giVerified: _isJardaluGiEligible,
          overrideTerritory: _outsideTerritory && _overrideAccepted,
          overrideReason: _overrideReasonCtrl.text.trim(),
        );

    if (!mounted) return;
    setState(() => _busy = false);

    if (newId != null) {
      await context.read<AgentState>().loadFarmers(agent);
      if (!mounted) return;
      _resetForm();
      setState(() => _msg = 'Farmer registered.');
    } else {
      setState(() => _msg = 'Registration failed — try again.');
    }
  }

  void _resetForm() {
    _nameCtrl.clear();
    _mobileCtrl.clear();
    _villageCtrl.clear();
    _areaCtrl.clear();
    _treeCountCtrl.clear();
    _treeAgeCtrl.clear();
    _lastYieldCtrl.clear();
    _overrideReasonCtrl.clear();
    setState(() {
      _selectedCrop = null;
      _selectedVariety = null;
      _bearing = 'Unknown';
      _irrigation = 'Drip';
      _outsideTerritory = false;
      _overrideAccepted = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final Agent? agent = context.watch<AuthState>().currentAgent;
    if (agent == null) return const SizedBox.shrink();

    final bool isJharkhand = agent.regionCode == RegionHelper.regionJH;
    // Variety dropdown uses the agent's regionCode for JH; otherwise it
    // falls back to the legacy sub-region (Konkan / Nashik / etc).
    final String varietyRegion =
        isJharkhand ? RegionHelper.regionJH : agent.region;
    final List<String> crops = RegionHelper.cropsForRegion(
      isJharkhand ? RegionHelper.regionJH : agent.region,
    );

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Form(
        key: _formKey,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            LanguageSelector(
              value: _locale,
              onChanged: (String? v) {
                if (v != null) setState(() => _localLocale = v);
              },
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _nameCtrl,
              decoration: InputDecoration(
                labelText: I18n.t('full_name', _locale),
                border: const OutlineInputBorder(),
              ),
              validator: (String? v) =>
                  (v == null || v.trim().isEmpty) ? 'Required' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _mobileCtrl,
              keyboardType: TextInputType.phone,
              inputFormatters: <TextInputFormatter>[
                FilteringTextInputFormatter.digitsOnly,
                LengthLimitingTextInputFormatter(10),
              ],
              decoration: InputDecoration(
                labelText: I18n.t('mobile_label', _locale),
                border: const OutlineInputBorder(),
              ),
              validator: (String? v) {
                final String s = (v ?? '').trim();
                return s.length == 10 ? null : '10 digits required';
              },
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _villageCtrl,
              decoration: InputDecoration(
                labelText: I18n.t('village', _locale),
                border: const OutlineInputBorder(),
              ),
              validator: (String? v) =>
                  (v == null || v.trim().isEmpty) ? 'Required' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _districtCtrl,
              decoration: InputDecoration(
                labelText: I18n.t('district', _locale),
                helperText: _lat == null
                    ? 'GPS unavailable — enter manually'
                    : 'GPS: ${_lat!.toStringAsFixed(3)}, ${_lng!.toStringAsFixed(3)}',
                border: const OutlineInputBorder(),
              ),
              onChanged: (_) => _evaluateTerritory(),
              validator: (String? v) =>
                  (v == null || v.trim().isEmpty) ? 'Required' : null,
            ),
            if (_outsideTerritory) ...<Widget>[
              const SizedBox(height: 12),
              Card(
                color: Colors.orange.shade50,
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Row(
                        children: <Widget>[
                          const Icon(Icons.warning, color: Colors.orange),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              I18n.t('outside_territory', _locale),
                              style:
                                  const TextStyle(fontWeight: FontWeight.w600),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      CheckboxListTile(
                        contentPadding: EdgeInsets.zero,
                        value: _overrideAccepted,
                        onChanged: (bool? v) =>
                            setState(() => _overrideAccepted = v ?? false),
                        title: const Text('Override and register anyway'),
                      ),
                      if (_overrideAccepted)
                        TextFormField(
                          controller: _overrideReasonCtrl,
                          decoration: InputDecoration(
                            labelText: I18n.t('override_reason', _locale),
                            border: const OutlineInputBorder(),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ],
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              value: _selectedCrop,
              decoration: InputDecoration(
                labelText: I18n.t('crop', _locale),
                border: const OutlineInputBorder(),
              ),
              items: crops
                  .map<DropdownMenuItem<String>>(
                    (String c) =>
                        DropdownMenuItem<String>(value: c, child: Text(c)),
                  )
                  .toList(),
              onChanged: (String? v) => setState(() {
                _selectedCrop = v;
                _selectedVariety = null;
              }),
              validator: (String? v) =>
                  (v == null || v.isEmpty) ? 'Required' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _areaCtrl,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration: InputDecoration(
                labelText: I18n.t('area_acres', _locale),
                border: const OutlineInputBorder(),
              ),
              validator: (String? v) {
                final double? d = double.tryParse((v ?? '').trim());
                return (d != null && d > 0) ? null : 'Enter a number > 0';
              },
            ),
            if (_selectedCrop != null) ...<Widget>[
              const SizedBox(height: 12),
              VarietyDropdown(
                region: varietyRegion,
                crop: _selectedCrop!,
                value: _selectedVariety,
                onChanged: (String? v) => setState(() => _selectedVariety = v),
                label: I18n.t('variety', _locale),
              ),
              if (_isJardaluGiEligible) ...<Widget>[
                const SizedBox(height: 8),
                _GiZoneBadge(label: I18n.t('gi_zone_badge', _locale)),
              ],
            ],
            if (_selectedCrop == 'Mango') ...<Widget>[
              const SizedBox(height: 12),
              TextFormField(
                controller: _treeCountCtrl,
                keyboardType: TextInputType.number,
                inputFormatters: <TextInputFormatter>[
                  FilteringTextInputFormatter.digitsOnly,
                ],
                decoration: InputDecoration(
                  labelText: I18n.t('tree_count', _locale),
                  border: const OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _treeAgeCtrl,
                keyboardType: TextInputType.number,
                inputFormatters: <TextInputFormatter>[
                  FilteringTextInputFormatter.digitsOnly,
                ],
                decoration: InputDecoration(
                  labelText: I18n.t('tree_age', _locale),
                  border: const OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                value: _irrigation,
                decoration: InputDecoration(
                  labelText: I18n.t('irrigation', _locale),
                  border: const OutlineInputBorder(),
                ),
                items: const <DropdownMenuItem<String>>[
                  DropdownMenuItem<String>(value: 'Drip', child: Text('Drip')),
                  DropdownMenuItem<String>(value: 'Flood', child: Text('Flood')),
                  DropdownMenuItem<String>(
                      value: 'Rain-fed', child: Text('Rain-fed')),
                  DropdownMenuItem<String>(value: 'None', child: Text('None')),
                ],
                onChanged: (String? v) =>
                    setState(() => _irrigation = v ?? 'Drip'),
              ),
              // Jharkhand-specific optional input — last season yield
              // per tree. Surfaces a baseline volume estimate for the
              // matching engine.
              if (isJharkhand) ...<Widget>[
                const SizedBox(height: 12),
                TextFormField(
                  controller: _lastYieldCtrl,
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true),
                  decoration: InputDecoration(
                    labelText: I18n.t('last_yield_kg_per_tree', _locale),
                    border: const OutlineInputBorder(),
                  ),
                ),
              ],
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                value: _bearing,
                decoration: InputDecoration(
                  labelText: I18n.t('bearing_year', _locale),
                  border: const OutlineInputBorder(),
                ),
                items: const <DropdownMenuItem<String>>[
                  DropdownMenuItem<String>(value: 'ON', child: Text('ON')),
                  DropdownMenuItem<String>(value: 'OFF', child: Text('OFF')),
                  DropdownMenuItem<String>(
                      value: 'Unknown', child: Text('Unknown')),
                ],
                onChanged: (String? v) =>
                    setState(() => _bearing = v ?? 'Unknown'),
              ),
            ],
            const SizedBox(height: 20),
            FilledButton(
              onPressed: _busy ? null : _submit,
              child: _busy
                  ? const SizedBox(
                      height: 18,
                      width: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : Text(I18n.t('submit', _locale)),
            ),
            if (_msg != null) ...<Widget>[
              const SizedBox(height: 12),
              Text(_msg!, textAlign: TextAlign.center),
            ],
          ],
        ),
      ),
    );
  }
}

/// Small green badge surfaced under the variety dropdown when the
/// Jardalu GI bbox check passes.
class _GiZoneBadge extends StatelessWidget {
  const _GiZoneBadge({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xFF2E7D32).withOpacity(0.12),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFF2E7D32), width: 1.4),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          const Icon(Icons.verified, color: Color(0xFF2E7D32), size: 18),
          const SizedBox(width: 6),
          Text(
            label,
            style: const TextStyle(
              color: Color(0xFF2E7D32),
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}
