using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace ShetMitraAdmin.Converters;

#nullable enable

/// <summary>
/// Maps <c>variety_collection_status</c> labels (and the related
/// <c>variety_source</c> badge labels) to a SolidColorBrush for badge
/// backgrounds in the variety collection surfaces.
/// </summary>
public sealed class VarietyStatusToBrushConverter : IValueConverter
{
    private static readonly SolidColorBrush Complete = new(Color.FromRgb(0x2E, 0x7D, 0x32));        // green
    private static readonly SolidColorBrush AwaitingReply = new(Color.FromRgb(0xF5, 0x7C, 0x00));   // orange
    private static readonly SolidColorBrush AgentRequired = new(Color.FromRgb(0xC6, 0x28, 0x28));   // red
    private static readonly SolidColorBrush Abandoned = new(Color.FromRgb(0x9E, 0x9E, 0x9E));       // grey
    private static readonly SolidColorBrush SourceFarmer = new(Color.FromRgb(0x1E, 0x88, 0xE5));    // blue
    private static readonly SolidColorBrush Clear = Brushes.Transparent;

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var label = value?.ToString()?.Trim().ToUpperInvariant() ?? "";
        return label switch
        {
            "COMPLETE" => Complete,
            "AWAITING_REPLY" => AwaitingReply,
            "AGENT_REQUIRED" => AgentRequired,
            "ABANDONED" => Abandoned,
            // variety_source badge tones
            "AGENT_VERIFIED" => Complete,
            "FARMER_REPORTED" => SourceFarmer,
            "AMED_HINT" => Abandoned,
            _ => Clear
        };
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
