using System;

namespace ShetMitraAdmin.Models;

#nullable enable

public sealed record AmedHistory
{
    public Guid Id { get; init; }
    public string Region { get; init; } = "";
    public string SeasonLabel { get; init; } = "";
    public int SeasonYearStart { get; init; }
    public string? CropType { get; init; }
    public double TotalAreaAcres { get; init; }
    public DateOnly? HarvestStartDate { get; init; }
    public DateOnly? HarvestPeakDate { get; init; }
    public DateOnly? HarvestEndDate { get; init; }
    public double EstimatedTotalVolumeMt { get; init; }
    public double? AvgPriceModalKg { get; init; }
}
