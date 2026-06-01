using System;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using ShetMitraAdmin.Models.Marketplace;
using ShetMitraAdmin.Services;

namespace ShetMitraAdmin.ViewModels;

#nullable enable

/// <summary>
/// View-model behind <see cref="Views.MarketplaceView"/> — the five-tab
/// Bagaan Sathi surface (Active Lots / Active Requirements / Matches /
/// Trades Completed / Aggregations). Each tab is backed by its own
/// <see cref="ObservableCollection{T}"/> and per-tab filter properties.
/// </summary>
public partial class MarketplaceViewModel : ObservableObject
{
    private readonly SupabaseService? _supabase;

    public ObservableCollection<MarketplaceLot> ActiveLots { get; } = new();
    public ObservableCollection<TraderRequirement> ActiveRequirements { get; } = new();
    public ObservableCollection<LotMatch> Matches { get; } = new();
    public ObservableCollection<FarmerTrade> TradesCompleted { get; } = new();
    public ObservableCollection<MarketplaceAggregation> Aggregations { get; } = new();

    // ─── Shared filters (region/commodity/grade/week/status) ──────────

    public ObservableCollection<string> RegionOptions { get; } = new()
    {
        "All", "MH", "JH"
    };

    public ObservableCollection<string> CommodityOptions { get; } = new()
    {
        "All", "Dry Grapes", "Pomegranate", "Mango", "Tomato", "Potato"
    };

    public ObservableCollection<string> GradeOptions { get; } = new()
    {
        "All", "A+", "A", "B", "C"
    };

    public ObservableCollection<string> WeekOptions { get; } = new()
    {
        "All", "W22", "W23", "W24"
    };

    public ObservableCollection<string> StatusOptions { get; } = new()
    {
        "All", "ACTIVE", "MATCHED", "SETTLED", "CANCELLED", "PROPOSED", "OPEN"
    };

    [ObservableProperty] private string lotsRegion = "All";
    [ObservableProperty] private string lotsCommodity = "All";
    [ObservableProperty] private string lotsGrade = "All";
    [ObservableProperty] private string lotsStatus = "All";

    [ObservableProperty] private string requirementsRegion = "All";
    [ObservableProperty] private string requirementsCommodity = "All";
    [ObservableProperty] private string requirementsGrade = "All";
    [ObservableProperty] private string requirementsStatus = "All";

    [ObservableProperty] private string matchesRegion = "All";
    [ObservableProperty] private string matchesCommodity = "All";
    [ObservableProperty] private string matchesStatus = "All";
    [ObservableProperty] private MarketplaceLot? selectedLot;

    [ObservableProperty] private string tradesRegion = "All";
    [ObservableProperty] private string tradesCommodity = "All";
    [ObservableProperty] private string tradesGrade = "All";
    [ObservableProperty] private string tradesWeek = "All";

    [ObservableProperty] private string aggregationsRegion = "All";
    [ObservableProperty] private string aggregationsCommodity = "All";
    [ObservableProperty] private string aggregationsGrade = "All";
    [ObservableProperty] private string aggregationsWeek = "All";

    [ObservableProperty] private string statusMessage = "";
    [ObservableProperty] private bool isBusy;

    public MarketplaceViewModel() : this(App.Supabase) { }

    public MarketplaceViewModel(SupabaseService? supabase)
    {
        _supabase = supabase;
        SeedDesignTime();
        _ = RefreshAsync();
    }

    private void SeedDesignTime()
    {
        var now = DateTime.UtcNow;
        ActiveLots.Add(new MarketplaceLot
        {
            Id = "seed-lot-1",
            FarmerName = "Suresh Patil",
            Region = "MH",
            Commodity = "Dry Grapes",
            Variety = "Thompson Seedless",
            Grade = "A",
            QuantityKg = 1800,
            AskPriceKg = 318,
            Status = "ACTIVE",
            ListedAt = now.AddHours(-6),
            WeekLabel = "W23"
        });
        ActiveLots.Add(new MarketplaceLot
        {
            Id = "seed-lot-2",
            FarmerName = "Anil Sinha",
            Region = "JH",
            Commodity = "Tomato",
            Grade = "B",
            QuantityKg = 950,
            AskPriceKg = 24,
            Status = "ACTIVE",
            ListedAt = now.AddHours(-22),
            WeekLabel = "W23"
        });

        ActiveRequirements.Add(new TraderRequirement
        {
            Id = "seed-req-1",
            TraderName = "Shah Commission Agents",
            Region = "MH",
            Commodity = "Dry Grapes",
            Grade = "A",
            RequiredQuantityKg = 5000,
            MaxPriceKg = 325,
            DeliveryWindow = "7 days",
            Status = "ACTIVE",
            CreatedAt = now.AddHours(-4)
        });

        Matches.Add(new LotMatch
        {
            Id = "seed-match-1",
            LotId = "seed-lot-1",
            FarmerName = "Suresh Patil",
            TraderName = "Shah Commission Agents",
            Commodity = "Dry Grapes",
            MatchedQuantityKg = 1800,
            MatchedPriceKg = 318,
            MatchScore = 0.92,
            Status = "PROPOSED",
            MatchedAt = now.AddMinutes(-30)
        });

        TradesCompleted.Add(new FarmerTrade
        {
            Id = "seed-trade-1",
            FarmerName = "Ramesh Jadhav",
            TraderName = "Bhandari Raisin Exports",
            Region = "MH",
            Commodity = "Dry Grapes",
            Grade = "A",
            TradedQuantityKg = 2200,
            SettledPriceKg = 312,
            MandiModalPriceKg = 295,
            PremiumPct = 5.8,
            PlatformFeeInr = 6864,
            SettledAt = now.AddDays(-2),
            WeekLabel = "W22"
        });

        Aggregations.Add(new MarketplaceAggregation
        {
            Id = "seed-agg-1",
            Region = "MH",
            Commodity = "Dry Grapes",
            Grade = "A",
            WeekLabel = "W23",
            FarmerCount = 14,
            TotalQuantityKg = 18400,
            AvgAskPriceKg = 312,
            Status = "OPEN"
        });
    }

    [RelayCommand]
    public async Task RefreshAsync()
    {
        if (_supabase is null)
        {
            StatusMessage = "Supabase service not initialized.";
            return;
        }

        IsBusy = true;
        try
        {
            await Task.WhenAll(
                RefreshLotsAsync(),
                RefreshRequirementsAsync(),
                RefreshMatchesAsync(),
                RefreshTradesAsync(),
                RefreshAggregationsAsync());

            StatusMessage = $"Refreshed at {DateTime.Now:HH:mm:ss}";
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"MarketplaceViewModel.RefreshAsync failed: {ex.Message}");
            StatusMessage = $"Refresh failed: {ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    private async Task RefreshLotsAsync()
    {
        if (_supabase is null) return;
        var rows = await _supabase.GetMarketplaceLots(new MarketplaceFilter
        {
            Region = LotsRegion,
            Commodity = LotsCommodity,
            Grade = LotsGrade,
            Status = LotsStatus
        });
        ActiveLots.Clear();
        foreach (var r in rows) ActiveLots.Add(r);
    }

    private async Task RefreshRequirementsAsync()
    {
        if (_supabase is null) return;
        var rows = await _supabase.GetTraderRequirements(new MarketplaceFilter
        {
            Region = RequirementsRegion,
            Commodity = RequirementsCommodity,
            Grade = RequirementsGrade,
            Status = RequirementsStatus
        });
        ActiveRequirements.Clear();
        foreach (var r in rows) ActiveRequirements.Add(r);
    }

    private async Task RefreshMatchesAsync()
    {
        if (_supabase is null) return;
        var lotId = SelectedLot?.Id;
        if (string.IsNullOrEmpty(lotId) && ActiveLots.Count > 0)
        {
            lotId = ActiveLots[0].Id;
        }
        if (string.IsNullOrEmpty(lotId))
        {
            Matches.Clear();
            return;
        }
        var rows = await _supabase.GetLotMatches(lotId);
        Matches.Clear();
        foreach (var r in rows) Matches.Add(r);
    }

    private async Task RefreshTradesAsync()
    {
        if (_supabase is null) return;
        var rows = await _supabase.GetFarmerTrades(new MarketplaceFilter
        {
            Region = TradesRegion,
            Commodity = TradesCommodity,
            Grade = TradesGrade,
            Week = TradesWeek
        });
        TradesCompleted.Clear();
        foreach (var r in rows) TradesCompleted.Add(r);
    }

    private async Task RefreshAggregationsAsync()
    {
        if (_supabase is null) return;
        var rows = await _supabase.GetMarketplaceAggregations(new MarketplaceFilter
        {
            Region = AggregationsRegion,
            Commodity = AggregationsCommodity,
            Grade = AggregationsGrade,
            Week = AggregationsWeek
        });
        Aggregations.Clear();
        foreach (var r in rows) Aggregations.Add(r);
    }

    [RelayCommand]
    public async Task RunMatchingAsync()
    {
        if (_supabase is null) return;
        IsBusy = true;
        try
        {
            var result = await _supabase.TriggerMatching();
            StatusMessage = "Matching engine run: " + result.GetRawText();
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            StatusMessage = $"Run matching failed: {ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    partial void OnLotsRegionChanged(string value) => _ = RefreshLotsAsync();
    partial void OnLotsCommodityChanged(string value) => _ = RefreshLotsAsync();
    partial void OnLotsGradeChanged(string value) => _ = RefreshLotsAsync();
    partial void OnLotsStatusChanged(string value) => _ = RefreshLotsAsync();

    partial void OnRequirementsRegionChanged(string value) => _ = RefreshRequirementsAsync();
    partial void OnRequirementsCommodityChanged(string value) => _ = RefreshRequirementsAsync();
    partial void OnRequirementsGradeChanged(string value) => _ = RefreshRequirementsAsync();
    partial void OnRequirementsStatusChanged(string value) => _ = RefreshRequirementsAsync();

    partial void OnSelectedLotChanged(MarketplaceLot? value) => _ = RefreshMatchesAsync();

    partial void OnTradesRegionChanged(string value) => _ = RefreshTradesAsync();
    partial void OnTradesCommodityChanged(string value) => _ = RefreshTradesAsync();
    partial void OnTradesGradeChanged(string value) => _ = RefreshTradesAsync();
    partial void OnTradesWeekChanged(string value) => _ = RefreshTradesAsync();

    partial void OnAggregationsRegionChanged(string value) => _ = RefreshAggregationsAsync();
    partial void OnAggregationsCommodityChanged(string value) => _ = RefreshAggregationsAsync();
    partial void OnAggregationsGradeChanged(string value) => _ = RefreshAggregationsAsync();
    partial void OnAggregationsWeekChanged(string value) => _ = RefreshAggregationsAsync();
}
