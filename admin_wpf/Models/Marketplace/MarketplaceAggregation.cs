using System.Text.Json.Serialization;

namespace ShetMitraAdmin.Models.Marketplace;

#nullable enable

/// <summary>
/// A multi-farmer aggregated supply pool — small lots in the same region/
/// commodity/grade rolled up so a single buyer can absorb the combined
/// volume. Populated by the matching engine.
/// </summary>
public sealed class MarketplaceAggregation
{
    [JsonPropertyName("id")] public string? Id { get; set; }
    [JsonPropertyName("region")] public string? Region { get; set; }
    [JsonPropertyName("commodity")] public string? Commodity { get; set; }
    [JsonPropertyName("variety")] public string? Variety { get; set; }
    [JsonPropertyName("grade")] public string? Grade { get; set; }
    [JsonPropertyName("week_label")] public string? WeekLabel { get; set; }
    [JsonPropertyName("farmer_count")] public int FarmerCount { get; set; }
    [JsonPropertyName("total_quantity_kg")] public double TotalQuantityKg { get; set; }
    [JsonPropertyName("avg_ask_price_kg")] public double? AvgAskPriceKg { get; set; }
    [JsonPropertyName("status")] public string? Status { get; set; }

    public string DisplayCommodity =>
        string.IsNullOrEmpty(Variety) ? Commodity ?? "" : $"{Commodity} ({Variety})";

    public string DisplayQuantity => $"{TotalQuantityKg:N0} kg";

    public string DisplayAvgAsk => AvgAskPriceKg.HasValue
        ? $"₹{AvgAskPriceKg.Value:N0}/kg"
        : "—";
}
