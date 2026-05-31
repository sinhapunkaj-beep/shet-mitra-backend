using System.Text.Json.Serialization;

namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// Aggregate analytics block consumed by the Trader Intelligence dashboard
/// header KPI cards and the Revenue tab. Sourced from the internal API
/// <c>GET /traders/analytics</c> (Trader Intelligence SDD §9), or computed
/// client-side from per-tier / per-status count queries when the internal
/// endpoint is not reachable.
/// </summary>
public sealed record TraderAnalytics
{
    [JsonPropertyName("total_traders")]
    public int TotalTraders { get; init; }

    [JsonPropertyName("active_subscribers")]
    public int ActiveSubscribers { get; init; }

    [JsonPropertyName("trial_users")]
    public int TrialUsers { get; init; }

    [JsonPropertyName("paused_users")]
    public int PausedUsers { get; init; }

    [JsonPropertyName("cancelled_users")]
    public int CancelledUsers { get; init; }

    [JsonPropertyName("mrr")]
    public double Mrr { get; init; }

    [JsonPropertyName("this_month_revenue")]
    public double ThisMonthRevenue { get; init; }

    [JsonPropertyName("last_month_revenue")]
    public double LastMonthRevenue { get; init; }

    [JsonPropertyName("by_tier")]
    public TierBreakdown ByTier { get; init; } = new(0, 0, 0);

    [JsonPropertyName("trial_conversion_rate_pct")]
    public double? TrialConversionRatePct { get; init; }

    [JsonPropertyName("avg_query_count_premium")]
    public double? AvgQueryCountPremium { get; init; }
}

/// <summary>
/// Subscriber count split by subscription tier — Basic / Standard / Premium.
/// </summary>
public sealed record TierBreakdown(
    [property: JsonPropertyName("basic")] int Basic,
    [property: JsonPropertyName("standard")] int Standard,
    [property: JsonPropertyName("premium")] int Premium);
