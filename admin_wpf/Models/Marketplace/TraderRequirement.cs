using System;
using System.Text.Json.Serialization;

namespace ShetMitraAdmin.Models.Marketplace;

#nullable enable

/// <summary>
/// One row in the <c>trader_requirements</c> table — an active demand
/// posted by a trader on Bagaan Sathi.
/// </summary>
public sealed class TraderRequirement
{
    [JsonPropertyName("id")] public string? Id { get; set; }
    [JsonPropertyName("trader_id")] public string? TraderId { get; set; }
    [JsonPropertyName("trader_name")] public string? TraderName { get; set; }
    [JsonPropertyName("region")] public string? Region { get; set; }
    [JsonPropertyName("commodity")] public string? Commodity { get; set; }
    [JsonPropertyName("variety")] public string? Variety { get; set; }
    [JsonPropertyName("grade")] public string? Grade { get; set; }
    [JsonPropertyName("required_quantity_kg")] public double RequiredQuantityKg { get; set; }
    [JsonPropertyName("max_price_kg")] public double? MaxPriceKg { get; set; }
    [JsonPropertyName("delivery_window")] public string? DeliveryWindow { get; set; }
    [JsonPropertyName("status")] public string? Status { get; set; }
    [JsonPropertyName("created_at")] public DateTime? CreatedAt { get; set; }

    public string DisplayCommodity =>
        string.IsNullOrEmpty(Variety) ? Commodity ?? "" : $"{Commodity} ({Variety})";

    public string DisplayQuantity => $"{RequiredQuantityKg:N0} kg";

    public string DisplayMaxPrice => MaxPriceKg.HasValue
        ? $"₹{MaxPriceKg.Value:N0}/kg"
        : "—";
}
