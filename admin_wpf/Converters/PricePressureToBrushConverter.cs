using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace ShetMitraAdmin.Converters;

#nullable enable

public sealed class PricePressureToBrushConverter : IValueConverter
{
    private static readonly SolidColorBrush High = new(Color.FromRgb(0xC6, 0x28, 0x28));
    private static readonly SolidColorBrush Medium = new(Color.FromRgb(0xEF, 0x6C, 0x00));
    private static readonly SolidColorBrush Low = new(Color.FromRgb(0x2E, 0x7D, 0x32));
    private static readonly SolidColorBrush Neutral = new(Color.FromRgb(0x9E, 0x9E, 0x9E));

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var label = value?.ToString()?.Trim().ToUpperInvariant() ?? "";
        return label switch
        {
            "HIGH" => High,
            "MEDIUM" => Medium,
            "LOW" => Low,
            _ => Neutral
        };
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
