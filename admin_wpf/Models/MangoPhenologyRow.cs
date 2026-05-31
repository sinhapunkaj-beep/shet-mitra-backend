using System;

namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// One row of the <c>mango_phenology_log</c> table — a snapshot of mango
/// phenology + bearing-year data for a single plot/season. Surfaced on the
/// Farmer Detail Mango Intelligence sub-section and on the Belt Intelligence
/// Konkan / Nashik / Vidarbha tabs.
/// </summary>
public sealed record MangoPhenologyRow
{
    public Guid PlotId { get; init; }
    public string SeasonLabel { get; init; } = "";
    public string BearingYear { get; init; } = "";
    public DateTime? FloweringStartDate { get; init; }
    public DateTime? FloweringPeakDate { get; init; }
    public double? FloweringIntensityPct { get; init; }
    public int FrostEventsCount { get; init; }
    public int HeatStressEventsCount { get; init; }
    public DateTime? FruitSetDate { get; init; }
    public double? FruitSetPct { get; init; }
    public double? PredictedYieldKgPerTree { get; init; }
}
