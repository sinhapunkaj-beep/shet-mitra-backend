using System;
using System.Text.Json.Serialization;

namespace ShetMitraAdmin.Models.Marketplace;

#nullable enable

/// <summary>
/// A completed marketplace trade — populated when a lot is settled
/// against a trader requirement. Backs the "Trades Completed" tab.
/// </summary>
public sealed class FarmerTrade
{
    [JsonPropertyName("id")] public string? Id { get; set; }
    [JsonPropertyName("lot_id")] public string? LotId { get; set; }
    [JsonPropertyName("match_id")] public string? MatchId { get; set; }
    [JsonPropertyName("farmer_id")] public string? FarmerId { get; set; }
    [JsonPropertyName("farmer_name")] public string? FarmerName { get; set; }
    [JsonPropertyName("trader_id")] public string? TraderId { get; set; }
    [JsonPropertyName("trader_name")] public string? TraderName { get; set; }
    [JsonPropertyName("region")] public string? Region { get; set; }
    [JsonPropertyName("commodity")] public string? Commodity { get; set; }
    [JsonPropertyName("grade")] public string? Grade { get; set; }
    [JsonPropertyName("traded_quantity_kg")] public double TradedQuantityKg { get; set; }
    [JsonPropertyName("settled_price_kg")] public double SettledPriceKg { get; set; }
    [JsonPropertyName("mandi_modal_price_kg")] public double? MandiModalPriceKg { get; set; }
    [JsonPropertyName("premium_pct")] public double? PremiumPct { get; set; }
    [JsonPropertyName("platform_fee_inr")] public double? PlatformFeeInr { get; set; }
    [JsonPropertyName("settled_at")] public DateTime? SettledAt { get; set; }
    [JsonPropertyName("week_label")] public string? WeekLabel { get; set; }

    public double GrossInr => TradedQuantityKg * SettledPriceKg;

    public string DisplayPremium => PremiumPct.HasValue
        ? $"{PremiumPct.Value:+0.0;-0.0;0.0}%"
        : "—";
}
