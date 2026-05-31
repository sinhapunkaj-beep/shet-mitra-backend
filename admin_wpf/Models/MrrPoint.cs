using System;

namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// One data point on the MRR trend line chart in the Revenue tab. The
/// <see cref="Month"/> value is always the 1st of the month (truncated)
/// so chart axes can label months uniformly.
/// </summary>
public sealed record MrrPoint(DateTime Month, double Mrr);
