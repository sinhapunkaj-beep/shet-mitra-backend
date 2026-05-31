using System;
using System.Text.Json.Serialization;

namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// One row in the Trader Intelligence Subscribers DataGrid. Mirrors the
/// <c>traders</c> table from Trader Intelligence SDD Section 4.1. PostgREST
/// returns snake_case columns so all properties carry an explicit
/// <see cref="JsonPropertyNameAttribute"/>.
/// </summary>
public sealed class TraderRow
{
    [JsonPropertyName("id")]
    public Guid Id { get; set; }

    [JsonPropertyName("full_name")]
    public string FullName { get; set; } = "";

    [JsonPropertyName("mobile")]
    public string Mobile { get; set; } = "";

    [JsonPropertyName("business_name")]
    public string? BusinessName { get; set; }

    [JsonPropertyName("district")]
    public string? District { get; set; }

    [JsonPropertyName("subscription_tier")]
    public string Tier { get; set; } = "BASIC";

    [JsonPropertyName("subscription_status")]
    public string Status { get; set; } = "TRIAL";

    [JsonPropertyName("subscription_started_at")]
    public DateTime? SubscriptionStartedAt { get; set; }

    [JsonPropertyName("monthly_amount")]
    public double MonthlyAmount { get; set; }

    [JsonPropertyName("query_count_this_month")]
    public int QueryCountThisMonth { get; set; }

    [JsonPropertyName("last_report_at")]
    public DateTime? LastReportAt { get; set; }
}
