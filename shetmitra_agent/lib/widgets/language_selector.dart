import 'package:flutter/material.dart';

import '../utils/i18n.dart';

/// Stateless language selector. The parent owns the locale state.
class LanguageSelector extends StatelessWidget {
  const LanguageSelector({
    super.key,
    required this.value,
    required this.onChanged,
    this.compact = false,
  });

  final String value;
  final ValueChanged<String?> onChanged;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final List<DropdownMenuItem<String>> items = I18n.supportedLocales
        .map<DropdownMenuItem<String>>(
          (String code) => DropdownMenuItem<String>(
            value: code,
            child: Text(I18n.localeName(code)),
          ),
        )
        .toList();

    if (compact) {
      return DropdownButton<String>(
        value: value,
        items: items,
        onChanged: onChanged,
        underline: const SizedBox.shrink(),
      );
    }

    return DropdownButtonFormField<String>(
      value: value,
      decoration: InputDecoration(
        labelText: I18n.t('language', value),
        border: const OutlineInputBorder(),
      ),
      items: items,
      onChanged: onChanged,
    );
  }
}
