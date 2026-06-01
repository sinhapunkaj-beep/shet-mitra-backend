using System.Text;

namespace ShetMitraAdmin.Models.Marketplace;

#nullable enable

/// <summary>
/// Query filter for the marketplace list endpoints. All properties are
/// optional — a null/empty value means "no constraint". The special
/// sentinel "All" is also treated as "no constraint" so it can be bound
/// directly to UI dropdowns.
/// </summary>
public sealed class MarketplaceFilter
{
    public string? Region { get; set; }
    public string? Commodity { get; set; }
    public string? Grade { get; set; }
    public string? Week { get; set; }
    public string? Status { get; set; }

    /// <summary>
    /// Builds a PostgREST / FastAPI compatible query string, e.g.
    /// <c>?region=MH&amp;commodity=Dry+Grapes</c>. Returns an empty string
    /// when no filter is active.
    /// </summary>
    public string ToQueryString()
    {
        var sb = new StringBuilder();
        Append(sb, "region", Region);
        Append(sb, "commodity", Commodity);
        Append(sb, "grade", Grade);
        Append(sb, "week", Week);
        Append(sb, "status", Status);
        return sb.Length == 0 ? string.Empty : "?" + sb.ToString();
    }

    private static void Append(StringBuilder sb, string key, string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return;
        if (string.Equals(value, "All", System.StringComparison.OrdinalIgnoreCase)) return;
        if (sb.Length > 0) sb.Append('&');
        sb.Append(System.Uri.EscapeDataString(key));
        sb.Append('=');
        sb.Append(System.Uri.EscapeDataString(value));
    }
}
