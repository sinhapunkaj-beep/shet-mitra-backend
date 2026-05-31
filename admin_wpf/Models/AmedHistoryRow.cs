using System;

namespace ShetMitraAdmin.Models;

#nullable enable

public sealed record AmedHistoryRow
{
    public string Season { get; init; } = "";
    public string Crop { get; init; } = "";
    public DateOnly? Sowing { get; init; }
    public DateOnly? Harvest { get; init; }
    public double Acres { get; init; }
}
