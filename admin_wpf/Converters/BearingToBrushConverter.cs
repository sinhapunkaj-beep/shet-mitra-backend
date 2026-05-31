using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace ShetMitraAdmin.Converters;

#nullable enable

/// <summary>
/// Maps a mango bearing-year label ("ON" / "OFF" / "UNKNOWN") to a pastel
/// SolidColorBrush used for the bearing-year badge on the Farmer Detail
/// Mango Intelligence sub-section and as the band fill for the Price
/// Analysis chart annotations.
/// </summary>
public sealed class BearingToBrushConverter : IValueConverter
{
    private static readonly SolidColorBrush On = new(Color.FromRgb(0x90, 0xCA, 0xF9));      // light blue
    private static readonly SolidColorBrush Off = new(Color.FromRgb(0xFF, 0xB7, 0x4D));     // light orange
    private static readonly SolidColorBrush Unknown = new(Color.FromRgb(0xE0, 0xE0, 0xE0)); // light grey
    private static readonly SolidColorBrush Clear = Brushes.Transparent;

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var label = value?.ToString()?.Trim().ToUpperInvariant() ?? "";
        return label switch
        {
            "ON" => On,
            "OFF" => Off,
            "UNKNOWN" => Unknown,
            _ => Clear
        };
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
