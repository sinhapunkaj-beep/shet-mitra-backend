using System;

namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// One row in the Alerts / Variety Collection queue. Combines the latest
/// <c>variety_responses</c> snapshot with the farmer's identity fields and
/// AMED detected crop.
/// </summary>
public sealed record VarietyQueueRow
{
    public string FarmerName { get; init; } = "";
    public string Mobile { get; init; } = "";
    public string AmedCrop { get; init; } = "";
    public string Status { get; init; } = "PENDING";
    public int Attempts { get; init; }
    public DateTime? LastAttempt { get; init; }
    public Guid FarmerId { get; init; }
    public Guid? PlotId { get; init; }
}
