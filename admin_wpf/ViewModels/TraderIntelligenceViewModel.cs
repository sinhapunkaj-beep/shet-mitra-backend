using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Data;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using LiveChartsCore;
using LiveChartsCore.SkiaSharpView;
using LiveChartsCore.SkiaSharpView.Painting;
using SkiaSharp;
using ShetMitraAdmin.Models;
using ShetMitraAdmin.Services;

namespace ShetMitraAdmin.ViewModels;

#nullable enable

/// <summary>
/// Backs the Trader Intelligence screen (Trader Intelligence SDD §8).
/// Three tabs share this VM:
///   Tab 1 — Subscribers (DataGrid + filters + per-row actions)
///   Tab 2 — Reports     (DataGrid + filters + preview pane)
///   Tab 3 — Revenue     (MRR trend / tier pie / monthly revenue bar)
/// Plus 4 KPI header cards (Total / Active / MRR / This-month revenue)
/// that sit above the TabControl and are always visible.
/// </summary>
public partial class TraderIntelligenceViewModel : ObservableObject
{
    private readonly SupabaseService? _supabase;
    private readonly List<TraderRow> _allSubscribers = new();
    private readonly List<IntelligenceReportRow> _allReports = new();

    // ── KPI / Header ─────────────────────────────────────────────
    [ObservableProperty] private TraderAnalytics? analytics;
    [ObservableProperty] private bool isLoading;

    // ── Subscribers tab ──────────────────────────────────────────
    public ObservableCollection<TraderRow> Subscribers { get; } = new();
    public ICollectionView FilteredSubscribers { get; }

    [ObservableProperty] private TraderRow? selectedTrader;
    [ObservableProperty] private string tierFilter = "ALL";
    [ObservableProperty] private string statusFilter = "ALL";

    // ── Reports tab ──────────────────────────────────────────────
    public ObservableCollection<IntelligenceReportRow> Reports { get; } = new();
    public ICollectionView FilteredReports { get; }

    [ObservableProperty] private IntelligenceReportRow? selectedReport;
    [ObservableProperty] private string? reportPreviewText;
    [ObservableProperty] private string reportTypeFilter = "ALL";
    [ObservableProperty] private string reportCommodityFilter = "ALL";

    // ── Revenue tab — LiveCharts2 series ─────────────────────────
    [ObservableProperty] private ISeries[] mrrSeries = Array.Empty<ISeries>();
    [ObservableProperty] private Axis[] mrrXAxes = Array.Empty<Axis>();
    [ObservableProperty] private Axis[] mrrYAxes = Array.Empty<Axis>();
    [ObservableProperty] private ISeries[] tierPieSeries = Array.Empty<ISeries>();
    [ObservableProperty] private ISeries[] revenueBarSeries = Array.Empty<ISeries>();
    [ObservableProperty] private Axis[] revenueBarXAxes = Array.Empty<Axis>();

    // Design-time / parameterless ctor for the XAML designer.
    public TraderIntelligenceViewModel() : this(App.Supabase) { }

    public TraderIntelligenceViewModel(SupabaseService? supabase)
    {
        _supabase = supabase;

        FilteredSubscribers = CollectionViewSource.GetDefaultView(Subscribers);
        FilteredSubscribers.Filter = SubscriberFilter;

        FilteredReports = CollectionViewSource.GetDefaultView(Reports);
        FilteredReports.Filter = ReportFilter;

        SeedDesignTime();
        _ = LoadAsync();
    }

    // ── Design-time seed so the WPF designer renders ─────────────
    private void SeedDesignTime()
    {
        Analytics = new TraderAnalytics
        {
            TotalTraders = 27,
            ActiveSubscribers = 18,
            TrialUsers = 6,
            PausedUsers = 2,
            CancelledUsers = 1,
            Mrr = 132000,
            ThisMonthRevenue = 132000,
            LastMonthRevenue = 118000,
            ByTier = new TierBreakdown(8, 7, 3),
            TrialConversionRatePct = 25.0,
            AvgQueryCountPremium = 12.4
        };

        BuildTierPie(Analytics);
        BuildPlaceholderTrendCharts();
    }

    // ── Data load (parallel) ─────────────────────────────────────
    [RelayCommand]
    public async Task LoadAsync()
    {
        if (_supabase is null) return;
        try
        {
            IsLoading = true;
            var tradersTask = _supabase.GetTradersAsync();
            var reportsTask = _supabase.GetIntelligenceReportsAsync();
            var analyticsTask = _supabase.GetTraderAnalyticsAsync();
            var mrrTask = _supabase.GetMrrTrendAsync();

            await Task.WhenAll(tradersTask, reportsTask, analyticsTask, mrrTask);

            // Subscribers
            _allSubscribers.Clear();
            _allSubscribers.AddRange(tradersTask.Result);
            Subscribers.Clear();
            foreach (var t in _allSubscribers) Subscribers.Add(t);

            // Reports
            _allReports.Clear();
            _allReports.AddRange(reportsTask.Result);
            Reports.Clear();
            foreach (var r in _allReports) Reports.Add(r);

            // Analytics
            Analytics = analyticsTask.Result;
            BuildTierPie(Analytics);

            // MRR trend + revenue bars
            BuildMrrChart(mrrTask.Result);
            BuildRevenueBars(mrrTask.Result);

            FilteredSubscribers.Refresh();
            FilteredReports.Refresh();
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"TraderIntelligenceViewModel.LoadAsync failed: {ex.Message}");
        }
        finally
        {
            IsLoading = false;
        }
    }

    [RelayCommand]
    public async Task RefreshAsync() => await LoadAsync();

    // ── Per-row commands ─────────────────────────────────────────

    [RelayCommand]
    private async Task SendReportNowAsync(Guid traderId)
    {
        // TODO wire to /intelligence/send-report/{report_id}?trader_id=... once
        // Agent 5 exposes the single-trader send endpoint. For now we just log
        // and keep the contract so the View bindings compile.
        Debug.WriteLine($"SendReportNowAsync called for trader {traderId}");
        await Task.CompletedTask;
    }

    [RelayCommand]
    private async Task TriggerWeeklyReportAsync(string commodity)
    {
        if (_supabase is null || string.IsNullOrWhiteSpace(commodity)) return;
        var ok = await _supabase.TriggerReportGenerationAsync(commodity, "WEEKLY");
        Debug.WriteLine($"TriggerWeeklyReportAsync({commodity}) -> {ok}");
        await LoadAsync();
    }

    [RelayCommand]
    private Task PreviewReportAsync(IntelligenceReportRow? row)
    {
        if (row is null)
        {
            ReportPreviewText = null;
            return Task.CompletedTask;
        }

        // Prefer the content already loaded with the row. If absent, drop a
        // placeholder so the preview pane shows something useful until the
        // dedicated /intelligence/reports/{id} fetch is wired up.
        ReportPreviewText = !string.IsNullOrWhiteSpace(row.ContentEnglish)
            ? row.ContentEnglish
            : $"[{row.ReportType}] {row.Commodity} — {row.Region ?? "—"} — {row.ReportDate:yyyy-MM-dd}\n" +
              $"Signal: {row.Signal ?? "—"}\n" +
              $"Day1: Rs {row.PriceForecastDay1:N0}/kg\n" +
              $"Day3: Rs {row.PriceForecastDay3:N0}/kg\n" +
              $"Day7: Rs {row.PriceForecastDay7:N0}/kg\n" +
              $"Confidence: {row.ConfidencePct:N0}%\n" +
              $"Recipients: {row.RecipientsCount}    Delivered: {row.DeliveredCount}\n\n" +
              "(Full report body will appear here once the report-detail fetch is wired.)";
        return Task.CompletedTask;
    }

    [RelayCommand]
    private Task ResendReportAsync(IntelligenceReportRow? row)
    {
        if (row is null) return Task.CompletedTask;
        // TODO wire to POST /intelligence/send-report/{report_id} once the
        // routes are finalized by Agent 5.
        Debug.WriteLine($"ResendReportAsync called for report {row.Id}");
        return Task.CompletedTask;
    }

    // ── Filter plumbing ──────────────────────────────────────────

    partial void OnTierFilterChanged(string value) => FilteredSubscribers.Refresh();
    partial void OnStatusFilterChanged(string value) => FilteredSubscribers.Refresh();
    partial void OnReportTypeFilterChanged(string value) => FilteredReports.Refresh();
    partial void OnReportCommodityFilterChanged(string value) => FilteredReports.Refresh();

    partial void OnSelectedReportChanged(IntelligenceReportRow? value)
    {
        // Selecting a row in the Reports DataGrid auto-populates the preview.
        _ = PreviewReportAsync(value);
    }

    private bool SubscriberFilter(object obj)
    {
        if (obj is not TraderRow row) return false;
        var tierOk = string.IsNullOrEmpty(TierFilter) ||
                     TierFilter.Equals("ALL", StringComparison.OrdinalIgnoreCase) ||
                     (row.Tier ?? "").Equals(TierFilter, StringComparison.OrdinalIgnoreCase);
        var statusOk = string.IsNullOrEmpty(StatusFilter) ||
                       StatusFilter.Equals("ALL", StringComparison.OrdinalIgnoreCase) ||
                       (row.Status ?? "").Equals(StatusFilter, StringComparison.OrdinalIgnoreCase);
        return tierOk && statusOk;
    }

    private bool ReportFilter(object obj)
    {
        if (obj is not IntelligenceReportRow row) return false;
        var typeOk = string.IsNullOrEmpty(ReportTypeFilter) ||
                     ReportTypeFilter.Equals("ALL", StringComparison.OrdinalIgnoreCase) ||
                     (row.ReportType ?? "").Equals(ReportTypeFilter, StringComparison.OrdinalIgnoreCase);
        var commodityOk = string.IsNullOrEmpty(ReportCommodityFilter) ||
                          ReportCommodityFilter.Equals("ALL", StringComparison.OrdinalIgnoreCase) ||
                          (row.Commodity ?? "").Equals(ReportCommodityFilter, StringComparison.OrdinalIgnoreCase);
        return typeOk && commodityOk;
    }

    // ── Chart builders ───────────────────────────────────────────

    private void BuildTierPie(TraderAnalytics? a)
    {
        if (a is null) return;
        TierPieSeries = new ISeries[]
        {
            new PieSeries<double>
            {
                Name = "Basic",
                Values = new double[] { a.ByTier.Basic },
                Fill = new SolidColorPaint(new SKColor(0x75, 0x75, 0x75))
            },
            new PieSeries<double>
            {
                Name = "Standard",
                Values = new double[] { a.ByTier.Standard },
                Fill = new SolidColorPaint(new SKColor(0x19, 0x76, 0xD2))
            },
            new PieSeries<double>
            {
                Name = "Premium",
                Values = new double[] { a.ByTier.Premium },
                Fill = new SolidColorPaint(new SKColor(0xFF, 0xB3, 0x00))
            }
        };
    }

    private void BuildMrrChart(List<MrrPoint> points)
    {
        var values = points.Select(p => p.Mrr).ToArray();
        var labels = points.Select(p => p.Month.ToString("MMM yy")).ToArray();

        MrrSeries = new ISeries[]
        {
            new LineSeries<double>
            {
                Name = "MRR (Rs)",
                Values = values,
                GeometrySize = 8,
                LineSmoothness = 0.2,
                Stroke = new SolidColorPaint(new SKColor(0x2E, 0x7D, 0x32), 3),
                Fill = new SolidColorPaint(new SKColor(0x2E, 0x7D, 0x32, 40))
            }
        };
        MrrXAxes = new[] { new Axis { Labels = labels, Name = "Month" } };
        MrrYAxes = new[] { new Axis { Name = "MRR (Rs)" } };
    }

    private void BuildRevenueBars(List<MrrPoint> points)
    {
        var values = points.Select(p => p.Mrr).ToArray();
        var labels = points.Select(p => p.Month.ToString("MMM yy")).ToArray();

        RevenueBarSeries = new ISeries[]
        {
            new ColumnSeries<double>
            {
                Name = "Monthly revenue (Rs)",
                Values = values,
                Fill = new SolidColorPaint(new SKColor(0xFF, 0xB3, 0x00))
            }
        };
        RevenueBarXAxes = new[] { new Axis { Labels = labels, Name = "Month" } };
    }

    private void BuildPlaceholderTrendCharts()
    {
        var today = DateTime.UtcNow.Date;
        var firstOfThisMonth = new DateTime(today.Year, today.Month, 1);
        var rng = new Random(2026);
        var points = new List<MrrPoint>();
        double mrr = 24000;
        for (var i = 11; i >= 0; i--)
        {
            var month = firstOfThisMonth.AddMonths(-i);
            mrr += 8500 + rng.Next(0, 4500);
            points.Add(new MrrPoint(month, Math.Round(mrr)));
        }
        BuildMrrChart(points);
        BuildRevenueBars(points);
    }
}
