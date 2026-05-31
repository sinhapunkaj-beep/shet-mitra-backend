using System;

namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// One row of the <c>mango_belt_data</c> table — a per-week belt aggregate
/// scoped to a region + variety. Drives the Konkan (Alphonso) / Nashik
/// (Kesar) / Vidarbha (Dasheri) tabs in Belt Intelligence and the Mango
/// stat card on the Dashboard.
/// </summary>
public sealed record MangoBeltRow
{
    public string Region { get; init; } = "";
    public string Variety { get; init; } = "";
    public DateTime FetchDate { get; init; }
    public int TotalFieldsDetected { get; init; }
    public double TotalAreaAcres { get; init; }
    public string? BearingYear { get; init; }
    public int FieldsHarvesting { get; init; }
    public double EstimatedVolumeMt { get; init; }
    public double? HealthPctGood { get; init; }
    public double? FloweringPct { get; init; }
    public double? FruitSetPct { get; init; }
}
