using System;

namespace ShetMitraAdmin.Models;

#nullable enable

public sealed record NdviAlert
{
    public Guid Id { get; init; }
    public string FarmerName { get; init; } = "";
    public string Severity { get; init; } = "";
    public string Message { get; init; } = "";
    public DateTime CreatedAt { get; init; }
}
