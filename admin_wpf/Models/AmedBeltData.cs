using System;

namespace ShetMitraAdmin.Models;

#nullable enable

public sealed record AmedBeltData
{
    public Guid Id { get; init; }
    public string Region { get; init; } = "";
    public DateOnly WeekStart { get; init; }
    public double ForecastVolumeMt { get; init; }
    public int FieldsHarvesting { get; init; }
    public double HealthGoodPct { get; init; }
    public double HealthModeratePct { get; init; }
    public double HealthStressedPct { get; init; }
    public double HealthCriticalPct { get; init; }
    public string? PricePressure { get; init; }
    public DateOnly FetchDate { get; init; }
}
