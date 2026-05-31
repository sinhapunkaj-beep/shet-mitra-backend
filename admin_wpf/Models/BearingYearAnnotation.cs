namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// One ON / OFF bearing-year annotation used to overlay coloured bands on
/// the Price Analysis history chart. <see cref="Price"/> is the modal price
/// for that year — kept so the band tooltip can show "ON 2024 — Rs 1450/kg".
/// </summary>
public sealed record BearingYearAnnotation(int Year, string BearingType, double Price);
