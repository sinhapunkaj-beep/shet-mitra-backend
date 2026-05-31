import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config.dart';

/// Thin wrapper around `package:http` that adds the Supabase anon-key
/// headers (`apikey` + `Authorization: Bearer ...`). Suitable for
/// PostgREST reads / writes against tables exposed by Supabase.
class SupabaseClient {
  SupabaseClient({http.Client? inner}) : _inner = inner ?? http.Client();

  final http.Client _inner;

  Uri _uri(String path, Map<String, String>? params) {
    final String cleanPath = path.startsWith('/') ? path : '/$path';
    final String fullPath = cleanPath.startsWith(AppConfig.restPrefix)
        ? cleanPath
        : '${AppConfig.restPrefix}$cleanPath';
    return Uri.parse('${AppConfig.supabaseUrl}$fullPath')
        .replace(queryParameters: params);
  }

  Map<String, String> _headers({bool write = false}) {
    final Map<String, String> h = <String, String>{
      'apikey': AppConfig.supabaseAnonKey,
      'Authorization': 'Bearer ${AppConfig.supabaseAnonKey}',
      'Accept': 'application/json',
    };
    if (write) {
      h['Content-Type'] = 'application/json';
      h['Prefer'] = 'return=representation';
    }
    return h;
  }

  /// GET request. Returns a List<dynamic> for PostgREST table queries.
  Future<List<dynamic>> get(String path, {Map<String, String>? params}) async {
    final http.Response res =
        await _inner.get(_uri(path, params), headers: _headers());
    _assertOk(res);
    final dynamic body = jsonDecode(res.body);
    if (body is List) return body;
    return <dynamic>[body];
  }

  /// POST request — used for inserts.
  Future<dynamic> post(String path, Map<String, dynamic> body) async {
    final http.Response res = await _inner.post(
      _uri(path, null),
      headers: _headers(write: true),
      body: jsonEncode(body),
    );
    _assertOk(res);
    if (res.body.isEmpty) return null;
    return jsonDecode(res.body);
  }

  /// PATCH request — used to update a row identified by a filter.
  Future<dynamic> patch(
    String path,
    Map<String, dynamic> body, {
    Map<String, String>? params,
  }) async {
    final http.Response res = await _inner.patch(
      _uri(path, params),
      headers: _headers(write: true),
      body: jsonEncode(body),
    );
    _assertOk(res);
    if (res.body.isEmpty) return null;
    return jsonDecode(res.body);
  }

  void _assertOk(http.Response res) {
    if (res.statusCode >= 200 && res.statusCode < 300) return;
    throw SupabaseException(res.statusCode, res.body);
  }

  void close() => _inner.close();
}

class SupabaseException implements Exception {
  SupabaseException(this.statusCode, this.body);
  final int statusCode;
  final String body;

  @override
  String toString() => 'SupabaseException($statusCode): $body';
}
