using System;
using System.Text.Json.Serialization;

namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// One row in the Trader Intelligence Reports DataGrid. Mirrors the
/// <c>intelligence_reports</c> table from Trader Intelligence SDD §4.2.
/// PostgREST returns snake_case columns.
/// </summary>
public sealed class IntelligenceReportRow
{
    [JsonPropertyName("id")]
    public Guid Id { get; set; }

    [JsonPropertyName("report_type")]
    public string ReportType { get; set; } = "";

    [JsonPropertyName("commodity")]
    public string Commodity { get; set; } = "";

    [JsonPropertyName("region")]
    public string? Region { get; set; }

    [JsonPropertyName("report_date")]
    public DateTime ReportDate { get; set; }

    [JsonPropertyName("signal")]
    public string? Signal { get; set; }

    [JsonPropertyName("price_forecast_day1")]
    public double? PriceForecastDay1 { get; set; }

    [JsonPropertyName("price_forecast_day3")]
    public double? PriceForecastDay3 { get; set; }

    [JsonPropertyName("price_forecast_day7")]
    public double? PriceForecastDay7 { get; set; }

    [JsonPropertyName("confidence_pct")]
    public double? ConfidencePct { get; set; }

    [JsonPropertyName("recipients_count")]
    public int RecipientsCount { get; set; }

    [JsonPropertyName("delivered_count")]
    public int DeliveredCount { get; set; }

    [JsonPropertyName("content_english")]
    public string? ContentEnglish { get; set; }
}
