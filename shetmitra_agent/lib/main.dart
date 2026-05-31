import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'screens/login_screen.dart';
import 'screens/home_screen.dart';
import 'state/auth_state.dart';
import 'state/agent_state.dart';

void main() {
  runApp(const ShetMitraAgentApp());
}

class ShetMitraAgentApp extends StatelessWidget {
  const ShetMitraAgentApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider<AuthState>(create: (_) => AuthState()..bootstrap()),
        ChangeNotifierProvider<AgentState>(create: (_) => AgentState()),
      ],
      child: MaterialApp(
        title: 'ShetMitra Agent',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF2E7D32)),
          useMaterial3: true,
        ),
        initialRoute: '/login',
        routes: {
          '/login': (_) => const LoginScreen(),
          '/home': (_) => const HomeScreen(),
        },
      ),
    );
  }
}
