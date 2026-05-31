using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using ShetMitraAdmin.Services;

namespace ShetMitraAdmin.ViewModels;

#nullable enable

public partial class DashboardViewModel : ObservableObject
{
    private readonly SupabaseService? _supabase;

    [ObservableProperty] private int totalFarmers;
    [ObservableProperty] private int activeAdvisories;
    [ObservableProperty] private int openAlerts;

    // Belt intelligence cards (SDD §7 Agent 6 Change 3)
    [ObservableProperty] private string beltCardTitle = "Tasgaon Belt — This Week";
    [ObservableProperty] private double beltEstimatedArrivalsMt;
    [ObservableProperty] private int beltFieldsHarvesting;
    [ObservableProperty] private double beltHealthGoodPct;
    [ObservableProperty] private string beltPricePressure = "HIGH";

    // Variety collection card (May 2026 spec)
    [ObservableProperty] private int varietyCollected;
    [ObservableProperty] private int varietyTotal;
    [ObservableProperty] private int varietyAwaiting;
    [ObservableProperty] private int varietyAgentRequired;

    // Mango stat card (May 2026 — Mango Agent 8 §9.5)
    [ObservableProperty] private int mangoTotalFarms;
    [ObservableProperty] private string mangoBearingYear = "ON";
    [ObservableProperty] private double mangoThisWeekVolumeMt;

    public double VarietyProgressPct => VarietyTotal == 0
        ? 0.0
        : (double)VarietyCollected / VarietyTotal * 100.0;

    public bool VarietyAgentBadgeVisible => VarietyAgentRequired > 0;

    partial void OnVarietyCollectedChanged(int value)
        => OnPropertyChanged(nameof(VarietyProgressPct));

    partial void OnVarietyTotalChanged(int value)
        => OnPropertyChanged(nameof(VarietyProgressPct));

    partial void OnVarietyAgentRequiredChanged(int value)
        => OnPropertyChanged(nameof(VarietyAgentBadgeVisible));

    public DashboardViewModel() : this(App.Supabase) { }

    public DashboardViewModel(SupabaseService? supabase)
    {
        _supabase = supabase;
        SeedDesignTime();
        _ = LoadAsync();
    }

    private void SeedDesignTime()
    {
        TotalFarmers = 102;
        ActiveAdvisories = 47;
        OpenAlerts = 6;

        BeltEstimatedArrivalsMt = 680;
        BeltFieldsHarvesting = 467;
        BeltHealthGoodPct = 63;
        BeltPricePressure = "HIGH";

        VarietyCollected = 46;
        VarietyTotal = 102;
        VarietyAwaiting = 33;
        VarietyAgentRequired = 14;

        MangoTotalFarms = 312;
        MangoBearingYear = "ON";
        MangoThisWeekVolumeMt = 412;
    }

    [RelayCommand]
    public async Task LoadAsync()
    {
        if (_supabase is null) return;

        var weeks = await _supabase.GetBeltData("Tasgaon", 1);
        if (weeks.Count > 0)
        {
            var w = weeks[0];
            BeltEstimatedArrivalsMt = w.ForecastVolumeMt;
            BeltFieldsHarvesting = w.FieldsHarvesting;
            BeltHealthGoodPct = w.HealthGoodPct;
            BeltPricePressure = w.PricePressure ?? "MEDIUM";
        }

        var variety = await _supabase.GetVarietyCollectionStatusAsync();
        VarietyCollected = variety.Complete;
        VarietyTotal = variety.Total;
        VarietyAwaiting = variety.AwaitingReply;
        VarietyAgentRequired = variety.AgentRequired;

        // Mango stat card — best-effort, falls back to design-time on failure.
        try
        {
            var konkan = await _supabase.GetMangoBeltDataAsync("Konkan", "Alphonso", limit: 1);
            if (konkan.Count > 0)
            {
                MangoTotalFarms = konkan[0].TotalFieldsDetected;
                MangoBearingYear = konkan[0].BearingYear ?? "UNKNOWN";
                MangoThisWeekVolumeMt = konkan[0].EstimatedVolumeMt;
            }
        }
        catch
        {
            // keep design-time placeholders
        }
    }
}
