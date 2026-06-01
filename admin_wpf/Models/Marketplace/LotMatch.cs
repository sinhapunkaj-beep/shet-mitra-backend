using System;
using System.Text.Json.Serialization;

namespace ShetMitraAdmin.Models.Marketplace;

#nullable enable

/// <summary>
/// A computed match between a lot and a trader requirement — produced by
/// the matching engine and surfaced via <c>/marketplace/matches/{lot_id}</c>.
/// </summary>
public sealed class LotMatch
{
    [JsonPropertyName("id")] public string? Id { get; set; }
    [JsonPropertyName("lot_id")] public string? LotId { get; set; }
    [JsonPropertyName("requirement_id")] public string? RequirementId { get; set; }
    [JsonPropertyName("trader_id")] public string? TraderId { get; set; }
    [JsonPropertyName("trader_name")] public string? TraderName { get; set; }
    [JsonPropertyName("farmer_id")] public string? FarmerId { get; set; }
    [JsonPropertyName("farmer_name")] public string? FarmerName { get; set; }
    [JsonPropertyName("commodity")] public string? Commodity { get; set; }
    [JsonPropertyName("matched_quantity_kg")] public double MatchedQuantityKg { get; set; }
    [JsonPropertyName("matched_price_kg")] public double? MatchedPriceKg { get; set; }
    [JsonPropertyName("match_score")] public double? MatchScore { get; set; }
    [JsonPropertyName("status")] public string? Status { get; set; }
    [JsonPropertyName("matched_at")] public DateTime? MatchedAt { get; set; }

    public string DisplayMatchScore => MatchScore.HasValue
        ? $"{MatchScore.Value * 100:0}%"
        : "—";

    public string DisplayPrice => MatchedPriceKg.HasValue
        ? $"₹{MatchedPriceKg.Value:N0}/kg"
        : "—";
}
