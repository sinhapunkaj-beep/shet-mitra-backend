using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace ShetMitraAdmin.Converters;

#nullable enable

public sealed class MismatchToBrushConverter : IValueConverter
{
    private static readonly SolidColorBrush MismatchBrush = new(Color.FromRgb(0xC6, 0x28, 0x28));
    private static readonly SolidColorBrush ClearBrush = Brushes.Transparent;

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var mismatch = value switch
        {
            bool b => b,
            double d => d > 20.0,
            int i => i > 20,
            _ => false
        };
        return mismatch ? MismatchBrush : ClearBrush;
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
