using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using LiveChartsCore;
using LiveChartsCore.SkiaSharpView;
using LiveChartsCore.SkiaSharpView.Painting;
using LiveChartsCore.SkiaSharpView.Painting.Effects;
using SkiaSharp;
using ShetMitraAdmin.Models;
using ShetMitraAdmin.Services;

namespace ShetMitraAdmin.ViewModels;

#nullable enable

public partial class BeltIntelligenceViewModel : ObservableObject
{
    private readonly SupabaseService? _supabase;
    private readonly string _region;

    // Top cards (SDD §7 Agent 6 NEW SCREEN — Belt Intelligence)
    [ObservableProperty] private int totalGrapeFields = 2847;
    [ObservableProperty] private int totalBeltAcres = 8234;
    [ObservableProperty] private double thisWeekArrivalsMt;
    [ObservableProperty] private double nextWeekArrivalsMt;
    [ObservableProperty] private double beltHealthGoodPct;
    [ObservableProperty] private string pricePressure = "HIGH";

    // Charts
    [ObservableProperty] private ISeries[] forecastSeries = Array.Empty<ISeries>();
    [ObservableProperty] private Axis[] forecastXAxes = Array.Empty<Axis>();
    [ObservableProperty] private Axis[] forecastYAxes = Array.Empty<Axis>();

    [ObservableProperty] private ISeries[] healthDistributionSeries = Array.Empty<ISeries>();
    [ObservableProperty] private Axis[] healthXAxes = Array.Empty<Axis>();
    [ObservableProperty] private Axis[] healthYAxes = Array.Empty<Axis>();

    public ObservableCollection<HistoricalComparisonRow> HistoricalComparison { get; } = new();
    public ObservableCollection<CropMismatch> MismatchReport { get; } = new();

    [ObservableProperty] private DateOnly lastRefreshed;
    [ObservableProperty] private DateOnly nextRefresh;

    // Region filter (Bagaan Sathi SDD §7) — the header dropdown above the
    // tab control. Controls visibility downstream; currently just a UI
    // selector while the data layer is wired.
    public ObservableCollection<string> RegionFilterOptions { get; } = new()
    {
        "All",
        "Maharashtra (ShetMitra)",
        "Jharkhand (Bagaan Sathi)"
    };

    [ObservableProperty] private string selectedRegionFilter = "All";

    // Mango belt tabs (May 2026 — Mango Agent 8 §9.4).
    public MangoBeltTabViewModel KonkanTab { get; }
    public MangoBeltTabViewModel NashikTab { get; }
    public MangoBeltTabViewModel VidarbhaTab { get; }

    // Jharkhand + Bihar belt (Bagaan Sathi SDD §7) — AMED harvest forecast
    // for the eastern belt. Data layer wires real forecasts later; for now
    // the tab renders deterministic stub rows.
    public JharkhandBeltTabViewModel JharkhandTab { get; }

    public BeltIntelligenceViewModel() : this(App.Supabase, "Tasgaon") { }

    public BeltIntelligenceViewModel(SupabaseService? supabase, string region)
    {
        _supabase = supabase;
        _region = region;
        LastRefreshed = DateOnly.FromDateTime(DateTime.UtcNow);
        NextRefresh = LastRefreshed.AddDays(14);

        KonkanTab = new MangoBeltTabViewModel(supabase, "Konkan", "Alphonso", "Konkan Belt — Alphonso");
        NashikTab = new MangoBeltTabViewModel(supabase, "Nashik", "Kesar", "Nashik Belt — Kesar");
        VidarbhaTab = new MangoBeltTabViewModel(supabase, "Vidarbha", "Dasheri", "Vidarbha Belt — Dasheri");
        JharkhandTab = new JharkhandBeltTabViewModel();

        SeedDesignTime();
        _ = RefreshAsync();
    }

    private void SeedDesignTime()
    {
        ThisWeekArrivalsMt = 680;
        NextWeekArrivalsMt = 540;
        BeltHealthGoodPct = 63;
        PricePressure = "HIGH";

        var weeks = new[] { "W1", "W2", "W3", "W4" };
        var forecast = new double[] { 680, 540, 420, 310 };
        var historicalAvg = new double[] { 620, 580, 460, 340 };

        ForecastSeries = new ISeries[]
        {
            new ColumnSeries<double>
            {
                Name = "AMED Forecast MT",
                Values = forecast,
                Fill = new SolidColorPaint(SKColors.Gold)
            },
            new LineSeries<double>
            {
                Name = "Historical Avg MT",
                Values = historicalAvg,
                GeometrySize = 8,
                LineSmoothness = 0,
                Fill = null,
                Stroke = new SolidColorPaint(SKColors.DimGray, 2)
                {
                    PathEffect = new DashEffect(new float[] { 6, 4 })
                }
            }
        };

        ForecastXAxes = new[]
        {
            new Axis { Labels = weeks, Name = "Week" }
        };
        ForecastYAxes = new[]
        {
            new Axis { Name = "MT" }
        };

        // Stacked health distribution per week
        var good = new double[] { 62, 60, 58, 55 };
        var moderate = new double[] { 22, 23, 24, 25 };
        var stressed = new double[] { 11, 12, 13, 14 };
        var critical = new double[] { 5, 5, 5, 6 };

        HealthDistributionSeries = new ISeries[]
        {
            new StackedColumnSeries<double>
            {
                Name = "Good",
                Values = good,
                Fill = new SolidColorPaint(SKColors.SeaGreen)
            },
            new StackedColumnSeries<double>
            {
                Name = "Moderate",
                Values = moderate,
                Fill = new SolidColorPaint(SKColors.Gold)
            },
            new StackedColumnSeries<double>
            {
                Name = "Stressed",
                Values = stressed,
                Fill = new SolidColorPaint(SKColors.OrangeRed)
            },
            new StackedColumnSeries<double>
            {
                Name = "Critical",
                Values = critical,
                Fill = new SolidColorPaint(SKColors.DarkRed)
            }
        };

        HealthXAxes = new[] { new Axis { Labels = weeks, Name = "Week" } };
        HealthYAxes = new[] { new Axis { Name = "% of fields", MinLimit = 0, MaxLimit = 100 } };

        HistoricalComparison.Clear();
        HistoricalComparison.Add(new HistoricalComparisonRow("2022-23", "Apr 21", 680, "118"));
        HistoricalComparison.Add(new HistoricalComparisonRow("2023-24", "Apr 19", 695, "134"));
        HistoricalComparison.Add(new HistoricalComparisonRow("2024-25", "Apr 14", 820, "298"));
        HistoricalComparison.Add(new HistoricalComparisonRow("2025-26 (forecast)", "Apr 14", 680, "TBD"));

        MismatchReport.Clear();
        MismatchReport.Add(new CropMismatch
        {
            FarmerId = Guid.NewGuid(),
            FarmerName = "Sample Farmer 12",
            RegisteredCrop = "Pomegranate",
            AmedDetectedCrop = "Dry Grapes",
            RegisteredAcres = 2.0,
            AmedAcres = 2.7,
            AreaDiffPct = 35,
            CropTypeMismatch = true,
            RecommendedAction = "Confirm with field officer"
        });
    }

    [RelayCommand]
    public async Task RefreshAsync()
    {
        if (_supabase is null) return;

        var weeks = await _supabase.GetBeltData(_region, 4);
        if (weeks.Count > 0)
        {
            ThisWeekArrivalsMt = weeks[0].ForecastVolumeMt;
            NextWeekArrivalsMt = weeks.Count > 1 ? weeks[1].ForecastVolumeMt : 0;
            BeltHealthGoodPct = weeks[0].HealthGoodPct;
            PricePressure = weeks[0].PricePressure ?? "MEDIUM";

            var labels = weeks.Select(w => w.WeekStart.ToString("MMM dd")).ToArray();
            var forecast = weeks.Select(w => w.ForecastVolumeMt).ToArray();
            var avg = weeks.Select(_ => weeks.Average(b => b.ForecastVolumeMt)).ToArray();

            ForecastSeries = new ISeries[]
            {
                new ColumnSeries<double>
                {
                    Name = "AMED Forecast MT",
                    Values = forecast,
                    Fill = new SolidColorPaint(SKColors.Gold)
                },
                new LineSeries<double>
                {
                    Name = "Historical Avg MT",
                    Values = avg,
                    Fill = null,
                    Stroke = new SolidColorPaint(SKColors.DimGray, 2)
                    {
                        PathEffect = new DashEffect(new float[] { 6, 4 })
                    }
                }
            };
            ForecastXAxes = new[] { new Axis { Labels = labels, Name = "Week" } };

            HealthDistributionSeries = new ISeries[]
            {
                new StackedColumnSeries<double>
                {
                    Name = "Good",
                    Values = weeks.Select(w => w.HealthGoodPct).ToArray(),
                    Fill = new SolidColorPaint(SKColors.SeaGreen)
                },
                new StackedColumnSeries<double>
                {
                    Name = "Moderate",
                    Values = weeks.Select(w => w.HealthModeratePct).ToArray(),
                    Fill = new SolidColorPaint(SKColors.Gold)
                },
                new StackedColumnSeries<double>
                {
                    Name = "Stressed",
                    Values = weeks.Select(w => w.HealthStressedPct).ToArray(),
                    Fill = new SolidColorPaint(SKColors.OrangeRed)
                },
                new StackedColumnSeries<double>
                {
                    Name = "Critical",
                    Values = weeks.Select(w => w.HealthCriticalPct).ToArray(),
                    Fill = new SolidColorPaint(SKColors.DarkRed)
                }
            };
            HealthXAxes = new[] { new Axis { Labels = labels, Name = "Week" } };
        }

        var history = await _supabase.GetAMEDHistory(_region);
        HistoricalComparison.Clear();
        foreach (var h in history)
        {
            HistoricalComparison.Add(new HistoricalComparisonRow(
                h.SeasonLabel,
                h.HarvestPeakDate?.ToString("MMM dd") ?? "—",
                h.EstimatedTotalVolumeMt,
                h.AvgPriceModalKg.HasValue ? h.AvgPriceModalKg.Value.ToString("0") : "TBD"));
        }

        var mismatches = await _supabase.GetCropMismatches();
        MismatchReport.Clear();
        foreach (var m in mismatches)
            MismatchReport.Add(m);

        LastRefreshed = DateOnly.FromDateTime(DateTime.UtcNow);
        NextRefresh = LastRefreshed.AddDays(14);
    }
}

public sealed record HistoricalComparisonRow(
    string Season,
    string PeakWeek,
    double PeakVolumeMt,
    string AvgPriceModalKg);

/// <summary>
/// Per-region mango belt tab payload — KPIs + forecast chart + health
/// distribution chart. Hosted by <see cref="BeltIntelligenceViewModel"/>
/// on the Konkan / Nashik / Vidarbha tabs added by Mango Agent 8 §9.4.
/// </summary>
public partial class MangoBeltTabViewModel : ObservableObject
{
    private readonly SupabaseService? _supabase;
    private readonly string _region;
    private readonly string _variety;

    [ObservableProperty] private string regionTitle = "";
    [ObservableProperty] private int totalFieldsDetected;
    [ObservableProperty] private double totalAreaAcres;
    [ObservableProperty] private double thisWeekVolumeMt;
    [ObservableProperty] private string bearingYear = "UNKNOWN";

    [ObservableProperty] private ISeries[] forecastSeries = Array.Empty<ISeries>();
    [ObservableProperty] private Axis[] forecastXAxes = Array.Empty<Axis>();
    [ObservableProperty] private Axis[] forecastYAxes = Array.Empty<Axis>();

    [ObservableProperty] private ISeries[] healthDistributionSeries = Array.Empty<ISeries>();
    [ObservableProperty] private Axis[] healthXAxes = Array.Empty<Axis>();
    [ObservableProperty] private Axis[] healthYAxes = Array.Empty<Axis>();

    public MangoBeltTabViewModel(SupabaseService? supabase, string region, string variety, string title)
    {
        _supabase = supabase;
        _region = region;
        _variety = variety;
        RegionTitle = title;
        SeedDesignTime();
        _ = LoadAsync();
    }

    private void SeedDesignTime()
    {
        TotalFieldsDetected = 1820;
        TotalAreaAcres = 5240;
        ThisWeekVolumeMt = 412;
        BearingYear = "ON";

        var weeks = new[] { "W-3", "W-2", "W-1", "W0" };
        var forecast = new double[] { 320, 360, 395, 412 };
        ForecastSeries = new ISeries[]
        {
            new ColumnSeries<double>
            {
                Name = "Volume MT",
                Values = forecast,
                Fill = new SolidColorPaint(SKColors.Goldenrod)
            }
        };
        ForecastXAxes = new[] { new Axis { Labels = weeks, Name = "Week" } };
        ForecastYAxes = new[] { new Axis { Name = "MT" } };

        var good = new double[] { 60, 62, 64, 66 };
        var moderate = new double[] { 22, 22, 20, 19 };
        var stressed = new double[] { 12, 11, 11, 10 };
        var critical = new double[] { 6, 5, 5, 5 };
        HealthDistributionSeries = new ISeries[]
        {
            new StackedColumnSeries<double> { Name = "Good", Values = good, Fill = new SolidColorPaint(SKColors.SeaGreen) },
            new StackedColumnSeries<double> { Name = "Moderate", Values = moderate, Fill = new SolidColorPaint(SKColors.Gold) },
            new StackedColumnSeries<double> { Name = "Stressed", Values = stressed, Fill = new SolidColorPaint(SKColors.OrangeRed) },
            new StackedColumnSeries<double> { Name = "Critical", Values = critical, Fill = new SolidColorPaint(SKColors.DarkRed) }
        };
        HealthXAxes = new[] { new Axis { Labels = weeks, Name = "Week" } };
        HealthYAxes = new[] { new Axis { Name = "% of fields", MinLimit = 0, MaxLimit = 100 } };
    }

    public async Task LoadAsync()
    {
        if (_supabase is null) return;

        var rows = await _supabase.GetMangoBeltDataAsync(_region, _variety, limit: 8);
        if (rows.Count == 0) return;

        var latest = rows[0];
        TotalFieldsDetected = latest.TotalFieldsDetected;
        TotalAreaAcres = latest.TotalAreaAcres;
        ThisWeekVolumeMt = latest.EstimatedVolumeMt;
        BearingYear = latest.BearingYear ?? "UNKNOWN";

        // Most recent N weeks for the chart, oldest first.
        var ordered = rows.OrderBy(r => r.FetchDate).ToList();
        var labels = ordered.Select(r => r.FetchDate.ToString("MMM dd")).ToArray();
        var volumes = ordered.Select(r => r.EstimatedVolumeMt).ToArray();

        ForecastSeries = new ISeries[]
        {
            new ColumnSeries<double>
            {
                Name = "Volume MT",
                Values = volumes,
                Fill = new SolidColorPaint(SKColors.Goldenrod)
            }
        };
        ForecastXAxes = new[] { new Axis { Labels = labels, Name = "Week" } };

        // Health distribution — placeholder modulation around HealthPctGood
        // because mango_belt_data only carries the single good %; TODO
        // surface mod/stressed/critical when Mango Agent 1 widens the table.
        var good = ordered.Select(r => r.HealthPctGood ?? 60).ToArray();
        var moderate = good.Select(g => Math.Max(0, 90 - g) * 0.4).ToArray();
        var stressed = good.Select(g => Math.Max(0, 90 - g) * 0.35).ToArray();
        var critical = good.Select(g => Math.Max(0, 90 - g) * 0.25).ToArray();
        HealthDistributionSeries = new ISeries[]
        {
            new StackedColumnSeries<double> { Name = "Good", Values = good, Fill = new SolidColorPaint(SKColors.SeaGreen) },
            new StackedColumnSeries<double> { Name = "Moderate", Values = moderate, Fill = new SolidColorPaint(SKColors.Gold) },
            new StackedColumnSeries<double> { Name = "Stressed", Values = stressed, Fill = new SolidColorPaint(SKColors.OrangeRed) },
            new StackedColumnSeries<double> { Name = "Critical", Values = critical, Fill = new SolidColorPaint(SKColors.DarkRed) }
        };
        HealthXAxes = new[] { new Axis { Labels = labels, Name = "Week" } };
    }
}

/// <summary>
/// Belt-intelligence tab payload for the Jharkhand + Bihar AMED region
/// (Bagaan Sathi SDD §7). Renders deterministic stub data until the
/// data layer wires the eastern-belt forecast endpoint.
/// </summary>
public partial class JharkhandBeltTabViewModel : ObservableObject
{
    [ObservableProperty] private string regionTitle = "Jharkhand + Bihar — AMED harvest forecast";
    [ObservableProperty] private int totalFieldsDetected = 4120;
    [ObservableProperty] private double totalAreaAcres = 9420;
    [ObservableProperty] private double thisWeekVolumeMt = 296;
    [ObservableProperty] private double nextWeekVolumeMt = 318;
    [ObservableProperty] private string topCommodity = "Tomato";
    [ObservableProperty] private string pricePressure = "MEDIUM";

    [ObservableProperty] private ISeries[] forecastSeries = Array.Empty<ISeries>();
    [ObservableProperty] private Axis[] forecastXAxes = Array.Empty<Axis>();
    [ObservableProperty] private Axis[] forecastYAxes = Array.Empty<Axis>();

    public ObservableCollection<JharkhandCommodityRow> CommodityRows { get; } = new();

    public JharkhandBeltTabViewModel()
    {
        SeedDesignTime();
    }

    private void SeedDesignTime()
    {
        var weeks = new[] { "W22", "W23", "W24", "W25" };
        var volumes = new double[] { 248, 296, 318, 305 };
        ForecastSeries = new ISeries[]
        {
            new ColumnSeries<double>
            {
                Name = "Forecast MT",
                Values = volumes,
                Fill = new SolidColorPaint(SKColors.OrangeRed)
            }
        };
        ForecastXAxes = new[] { new Axis { Labels = weeks, Name = "Week" } };
        ForecastYAxes = new[] { new Axis { Name = "MT" } };

        CommodityRows.Clear();
        CommodityRows.Add(new JharkhandCommodityRow(
            "Tomato", "Ranchi / Hazaribagh", 1820, 4180, 142, 22));
        CommodityRows.Add(new JharkhandCommodityRow(
            "Potato", "Patna / Nalanda", 1280, 3260, 96, 14));
        CommodityRows.Add(new JharkhandCommodityRow(
            "Brinjal", "Gaya / Jamui", 612, 1140, 38, 18));
        CommodityRows.Add(new JharkhandCommodityRow(
            "Cauliflower", "Begusarai", 408, 840, 20, 16));
    }
}

public sealed record JharkhandCommodityRow(
    string Commodity,
    string Belt,
    int FieldsDetected,
    double TotalAreaAcres,
    double ThisWeekVolumeMt,
    double ModalPriceKg);
