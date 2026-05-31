namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// Aggregate counts of farmers per <c>variety_collection_status</c> bucket
/// surfaced on the Dashboard and Alerts screens.
/// </summary>
public sealed record VarietyCollectionSummary
{
    public int Complete { get; init; }
    public int AwaitingReply { get; init; }
    public int Abandoned { get; init; }
    public int AgentRequired { get; init; }
    public int Total { get; init; }

    public double CompletionPct => Total == 0 ? 0.0 : (double)Complete / Total * 100.0;
}
