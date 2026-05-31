using System;

namespace ShetMitraAdmin.Models;

#nullable enable

public sealed record AmedReading
{
    public Guid Id { get; init; }
    public Guid FarmerId { get; init; }
    public string? CropType { get; init; }
    public double Confidence { get; init; }
    public double FieldSizeAcres { get; init; }
    public DateOnly? SowingDate { get; init; }
    public DateOnly? PredictedHarvestDate { get; init; }
    public string? GrowthStage { get; init; }
    public DateOnly? LastIrrigationDate { get; init; }
    public DateOnly FetchDate { get; init; }
}
