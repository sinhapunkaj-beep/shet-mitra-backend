namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// Combined variety + Brix/premium snapshot for a single farmer. Built from
/// the <c>farmers</c> + <c>farm_plots</c> join and enriched with values from
/// <c>data/variety_config.json</c>.
/// </summary>
public sealed record FarmerVarietyDetails
{
    public string? Variety { get; init; }
    public string? VarietySource { get; init; }
    public string VarietyCollectionStatus { get; init; } = "PENDING";
    public int? BrixTargetMin { get; init; }
    public int? BrixTargetMax { get; init; }
    public bool IsPremiumVariety { get; init; }
    public double? PremiumPct { get; init; }
}
