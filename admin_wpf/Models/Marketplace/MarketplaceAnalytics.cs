using System.Text.Json.Serialization;

namespace ShetMitraAdmin.Models.Marketplace;

#nullable enable

/// <summary>
/// Aggregated marketplace KPIs returned by
/// <c>GET /marketplace/analytics</c>. Drives the marketplace stat cards
/// on the main Dashboard.
/// </summary>
public sealed class MarketplaceAnalytics
{
    [JsonPropertyName("active_lots_count")] public int ActiveLotsCount { get; set; }
    [JsonPropertyName("active_lots_total_kg")] public double ActiveLotsTotalKg { get; set; }
    [JsonPropertyName("active_requirements_count")] public int ActiveRequirementsCount { get; set; }
    [JsonPropertyName("matches_this_week")] public int MatchesThisWeek { get; set; }
    [JsonPropertyName("trades_completed")] public int TradesCompleted { get; set; }
    [JsonPropertyName("avg_premium_pct")] public double AvgPremiumPct { get; set; }
    [JsonPropertyName("platform_fees_this_month_inr")] public double PlatformFeesThisMonthInr { get; set; }
}
