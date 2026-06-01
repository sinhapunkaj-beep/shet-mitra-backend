using System;
using System.Text.Json.Serialization;

namespace ShetMitraAdmin.Models.Marketplace;

#nullable enable

/// <summary>
/// One row in the <c>marketplace_lots</c> table — a farmer lot offered
/// for sale on Bagaan Sathi. Mirrors the FastAPI <c>/marketplace/lots</c>
/// payload (SDD §7 Marketplace surface).
/// </summary>
public sealed class MarketplaceLot
{
    [JsonPropertyName("id")] public string? Id { get; set; }
    [JsonPropertyName("farmer_id")] public string? FarmerId { get; set; }
    [JsonPropertyName("farmer_name")] public string? FarmerName { get; set; }
    [JsonPropertyName("region")] public string? Region { get; set; }
    [JsonPropertyName("commodity")] public string? Commodity { get; set; }
    [JsonPropertyName("variety")] public string? Variety { get; set; }
    [JsonPropertyName("grade")] public string? Grade { get; set; }
    [JsonPropertyName("quantity_kg")] public double QuantityKg { get; set; }
    [JsonPropertyName("ask_price_kg")] public double? AskPriceKg { get; set; }
    [JsonPropertyName("min_price_kg")] public double? MinPriceKg { get; set; }
    [JsonPropertyName("status")] public string? Status { get; set; }
    [JsonPropertyName("listed_at")] public DateTime? ListedAt { get; set; }
    [JsonPropertyName("week_label")] public string? WeekLabel { get; set; }

    public string DisplayCommodity =>
        string.IsNullOrEmpty(Variety) ? Commodity ?? "" : $"{Commodity} ({Variety})";

    public string DisplayQuantity => $"{QuantityKg:N0} kg";

    public string DisplayAsk => AskPriceKg.HasValue
        ? $"₹{AskPriceKg.Value:N0}/kg"
        : "—";
}
