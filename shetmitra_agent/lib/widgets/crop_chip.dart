import 'package:flutter/material.dart';

/// Small coloured chip used in the farmers list and belt cards.
class CropChip extends StatelessWidget {
  const CropChip({super.key, required this.crop, this.variety});

  final String crop;
  final String? variety;

  Color _colorFor(String c) {
    return switch (c.toLowerCase()) {
      'mango' => const Color(0xFFFFB300),
      'grapes' || 'grape' => const Color(0xFF7B1FA2),
      'pomegranate' => const Color(0xFFC62828),
      _ => const Color(0xFF455A64),
    };
  }

  @override
  Widget build(BuildContext context) {
    final Color bg = _colorFor(crop);
    final String label = variety != null && variety!.isNotEmpty
        ? '$crop · $variety'
        : crop;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: bg.withOpacity(0.15),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: bg.withOpacity(0.4)),
      ),
      child: Text(
        label,
        style: TextStyle(color: bg, fontWeight: FontWeight.w600, fontSize: 12),
      ),
    );
  }
}
