using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace ShetMitraAdmin.Converters;

#nullable enable

/// <summary>
/// Maps a trader's <c>subscription_tier</c> (or trial status) to a brush used
/// as the badge background in the Trader Intelligence Subscribers DataGrid.
/// </summary>
public sealed class TierToBrushConverter : IValueConverter
{
    private static readonly SolidColorBrush Premium  = new(Color.FromRgb(0xFF, 0xB3, 0x00)); // gold
    private static readonly SolidColorBrush Standard = new(Color.FromRgb(0x19, 0x76, 0xD2)); // blue
    private static readonly SolidColorBrush Basic    = new(Color.FromRgb(0x75, 0x75, 0x75)); // grey
    private static readonly SolidColorBrush Trial    = new(Color.FromRgb(0xF5, 0x7C, 0x00)); // orange
    private static readonly SolidColorBrush Clear    = Brushes.Transparent;

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var label = value?.ToString()?.Trim().ToUpperInvariant() ?? "";
        return label switch
        {
            "PREMIUM"  => Premium,
            "STANDARD" => Standard,
            "BASIC"    => Basic,
            "TRIAL"    => Trial,
            _          => Clear
        };
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
