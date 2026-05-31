using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using LiveChartsCore;
using LiveChartsCore.SkiaSharpView;
using LiveChartsCore.SkiaSharpView.Painting;
using SkiaSharp;
using ShetMitraAdmin.Services;

namespace ShetMitraAdmin.ViewModels;

#nullable enable

/// <summary>
/// Backing ViewModel for the Price Analysis screen. Surfaces a commodity →
/// variety → mandi cascade and a LiveCharts2 line chart of price history.
/// Mango selections add a variety dropdown and overlay ON / OFF bearing-year
/// bands on the chart via <see cref="BearingBands"/>.
/// </summary>
public partial class PriceAnalysisViewModel : ObservableObject
{
    private readonly SupabaseService? _supabase;

    public ObservableCollection<string> AvailableCommodities { get; } = new()
    {
        "Dry Grapes",
        "Pomegranate",
        "Mango"
    };

    public ObservableCollection<string> AvailableVarieties { get; } = new();
    public ObservableCollection<string> AvailableMandis { get; } = new();

    [ObservableProperty] private string selectedCommodity = "Dry Grapes";
    [ObservableProperty] private string? selectedVariety;
    [ObservableProperty] private string? selectedMandi;

    [ObservableProperty] private bool isMangoSelected;

    [ObservableProperty] private ISeries[] priceChartSeries = Array.Empty<ISeries>();
    [ObservableProperty] private Axis[] priceChartXAxes = Array.Empty<Axis>();
    [ObservableProperty] private Axis[] priceChartYAxes = Array.Empty<Axis>();
    [ObservableProperty] private List<RectangularSection> bearingBands = new();
    [ObservableProperty] private RectangularSection[] bearingBandsArray = Array.Empty<RectangularSection>();

    [ObservableProperty] private string? statusMessage;

    public PriceAnalysisViewModel() : this(App.Supabase) { }

    public PriceAnalysisViewModel(SupabaseService? supabase)
    {
        _supabase = supabase;
        RecomputeVarieties();
        RecomputeMandis();
        LoadPriceHistory();
    }

    partial void OnSelectedCommodityChanged(string value)
    {
        IsMangoSelected = string.Equals(value, "Mango", StringComparison.OrdinalIgnoreCase);
        RecomputeVarieties();
        RecomputeMandis();
        LoadPriceHistory();
    }

    partial void OnSelectedVarietyChanged(string? value)
    {
        RecomputeMandis();
        LoadPriceHistory();
    }

    partial void OnSelectedMandiChanged(string? value)
    {
        LoadPriceHistory();
    }

    private void RecomputeVarieties()
    {
        AvailableVarieties.Clear();
        if (string.Equals(SelectedCommodity, "Mango", StringComparison.OrdinalIgnoreCase))
        {
            AvailableVarieties.Add("Alphonso");
            AvailableVarieties.Add("Kesar");
            AvailableVarieties.Add("Dasheri");
            AvailableVarieties.Add("Totapuri");
            AvailableVarieties.Add("Banganapalli");
            if (string.IsNullOrEmpty(SelectedVariety) ||
                !AvailableVarieties.Contains(SelectedVariety!))
            {
                SelectedVariety = AvailableVarieties[0];
            }
        }
        else
        {
            SelectedVariety = null;
        }
    }

    private void RecomputeMandis()
    {
        AvailableMandis.Clear();
        if (IsMangoSelected)
        {
            foreach (var m in MandisForVariety(SelectedVariety))
            {
                AvailableMandis.Add(m);
            }
        }
        else if (string.Equals(SelectedCommodity, "Pomegranate", StringComparison.OrdinalIgnoreCase))
        {
            AvailableMandis.Add("Solapur");
            AvailableMandis.Add("Sangli");
            AvailableMandis.Add("Nashik");
        }
        else
        {
            AvailableMandis.Add("Tasgaon");
            AvailableMandis.Add("Sangli");
            AvailableMandis.Add("Pandharpur");
        }

        if (AvailableMandis.Count > 0 &&
            (string.IsNullOrEmpty(SelectedMandi) || !AvailableMandis.Contains(SelectedMandi!)))
        {
            SelectedMandi = AvailableMandis[0];
        }
    }

    private static IEnumerable<string> MandisForVariety(string? variety) => variety switch
    {
        "Alphonso" => new[] { "Ratnagiri", "Devgad", "Vashi" },
        "Kesar" => new[] { "Aurangabad", "Nashik", "Pune" },
        "Dasheri" => new[] { "Nagpur", "Amravati" },
        "Totapuri" => new[] { "Latur", "Solapur" },
        "Banganapalli" => new[] { "Sangli", "Kolhapur" },
        _ => new[] { "Ratnagiri", "Devgad", "Vashi" }
    };

    /// <summary>
    /// Recomputes the price-history series and (for Mango) the ON / OFF
    /// bearing-year band overlay. Currently uses synthetic data — TODO
    /// rewire to a real <c>mandi_prices</c> query when the table is live.
    /// </summary>
    [RelayCommand]
    public void LoadPriceHistory()
    {
        // TODO: replace with SupabaseService.GetPriceHistoryAsync(...) once
        // the mandi_prices table is wired by Mango Agent 7.
        _ = _supabase;

        var months = new[]
        {
            "Jun '23", "Sep '23", "Dec '23", "Mar '24",
            "Jun '24", "Sep '24", "Dec '24", "Mar '25",
            "Jun '25", "Sep '25", "Dec '25", "Mar '26"
        };

        var (values, colorHex) = SelectedCommodity switch
        {
            "Mango" => (BuildMangoSeriesForVariety(SelectedVariety), "#FFB300"),
            "Pomegranate" => (BuildPomegranateSeries(), "#C62828"),
            _ => (BuildDryGrapesSeries(), "#6A1B9A")
        };

        var color = SKColor.Parse(colorHex.TrimStart('#'));
        PriceChartSeries = new ISeries[]
        {
            new LineSeries<double>
            {
                Name = $"{SelectedCommodity} — {SelectedMandi}",
                Values = values,
                Fill = null,
                GeometrySize = 6,
                LineSmoothness = 0.4,
                Stroke = new SolidColorPaint(color, 3)
            }
        };

        PriceChartXAxes = new[] { new Axis { Labels = months, Name = "Month" } };
        PriceChartYAxes = new[] { new Axis { Name = "Rs / kg" } };

        BearingBands = IsMangoSelected ? BuildBearingBands(months.Length) : new List<RectangularSection>();
        BearingBandsArray = BearingBands.ToArray();

        StatusMessage = IsMangoSelected
            ? $"Showing {SelectedVariety} mango prices at {SelectedMandi} with ON / OFF bearing year overlay."
            : $"Showing {SelectedCommodity} prices at {SelectedMandi}.";
    }

    private static double[] BuildDryGrapesSeries()
        => new double[] { 118, 122, 128, 132, 134, 142, 168, 215, 280, 298, 312, 320 };

    private static double[] BuildPomegranateSeries()
        => new double[] { 52, 58, 61, 64, 68, 71, 75, 78, 80, 82, 79, 74 };

    private static double[] BuildMangoSeriesForVariety(string? variety)
    {
        // Alphonso peaks higher than the others; bearing-year cycle is the
        // dominant pattern — ON years (2024) cheaper, OFF years (2025) higher.
        var baseLine = variety switch
        {
            "Alphonso" => 1400.0,
            "Kesar" => 350.0,
            "Dasheri" => 220.0,
            "Totapuri" => 90.0,
            "Banganapalli" => 180.0,
            _ => 1400.0
        };
        // Two seasonal humps per year + ON/OFF swing.
        return new[]
        {
            baseLine * 0.55,
            baseLine * 0.40,
            baseLine * 0.45,
            baseLine * 0.90,
            baseLine * 0.70,
            baseLine * 0.50,
            baseLine * 0.55,
            baseLine * 1.00,
            baseLine * 0.85,
            baseLine * 0.65,
            baseLine * 0.75,
            baseLine * 1.20
        };
    }

    /// <summary>
    /// Builds rectangular bands across the X-axis index range — alternating
    /// ON (light blue) and OFF (light orange) per Mango Agent 8 §9.1 spec.
    /// The current season (2025-26) is assumed ON; the prior season OFF.
    /// </summary>
    private static List<RectangularSection> BuildBearingBands(int monthCount)
    {
        // 12 months across two bearing seasons — split in the middle.
        // First half = OFF (2024 — index 0..5), second half = ON (2025 — 6..11).
        var off = new SKColor(0xFF, 0xB7, 0x4D, 0x55); // 33% alpha
        var on = new SKColor(0x90, 0xCA, 0xF9, 0x55);

        var midpoint = monthCount / 2.0;
        return new List<RectangularSection>
        {
            new RectangularSection
            {
                Xi = -0.5,
                Xj = midpoint - 0.5,
                Fill = new SolidColorPaint(off),
                Label = "OFF 2024",
                LabelPaint = new SolidColorPaint(new SKColor(0x33, 0x33, 0x33))
            },
            new RectangularSection
            {
                Xi = midpoint - 0.5,
                Xj = monthCount - 0.5,
                Fill = new SolidColorPaint(on),
                Label = "ON 2025",
                LabelPaint = new SolidColorPaint(new SKColor(0x33, 0x33, 0x33))
            }
        };
    }
}
