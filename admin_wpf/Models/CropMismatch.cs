using System;

namespace ShetMitraAdmin.Models;

#nullable enable

public sealed record CropMismatch
{
    public Guid FarmerId { get; init; }
    public string FarmerName { get; init; } = "";
    public string RegisteredCrop { get; init; } = "";
    public string AmedDetectedCrop { get; init; } = "";
    public double RegisteredAcres { get; init; }
    public double AmedAcres { get; init; }
    public double AreaDiffPct { get; init; }
    public bool CropTypeMismatch { get; init; }
    public string? RecommendedAction { get; init; }
}
