using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using ShetMitraAdmin.Models;
using ShetMitraAdmin.Services;

namespace ShetMitraAdmin.ViewModels;

#nullable enable

public partial class AlertsViewModel : ObservableObject
{
    private readonly SupabaseService? _supabase;

    public ObservableCollection<CropMismatch> CropMismatches { get; } = new();
    public ObservableCollection<CropMismatch> AreaMismatches { get; } = new();
    public ObservableCollection<NdviAlert> NdviAlerts { get; } = new();
    public ObservableCollection<VarietyQueueRow> VarietyQueue { get; } = new();

    [ObservableProperty] private int cropMismatchCount;
    [ObservableProperty] private int areaMismatchCount;
    [ObservableProperty] private int ndviAlertCount;
    [ObservableProperty] private int selectedTabIndex;

    // Variety collection (May 2026 spec)
    [ObservableProperty] private VarietyCollectionSummary? varietySummary;
    [ObservableProperty] private VarietyQueueRow? selectedQueueRow;
    [ObservableProperty] private ManualVarietyEntry manualEntry = new();
    [ObservableProperty] private bool manualFormVisible;

    // Crop + region filters (May 2026 — Mango Agent 8 §9.3).
    // Filtering is currently client-side. The Crop options include "Mango"
    // so Mango farmers route into the same queue; region tracks the new
    // farmers.region column added by Mango Agent 1.
    public ObservableCollection<string> AvailableCropFilters { get; } = new()
    {
        "All",
        "Dry Grapes",
        "Pomegranate",
        "Mango"
    };

    public ObservableCollection<string> AvailableRegionFilters { get; } = new()
    {
        "All",
        "Konkan",
        "Marathwada",
        "Vidarbha",
        "Other"
    };

    [ObservableProperty] private string selectedCropFilter = "All";
    [ObservableProperty] private string selectedRegionFilter = "All";

    public ObservableCollection<VarietyQueueRow> FilteredVarietyQueue { get; } = new();

    partial void OnSelectedCropFilterChanged(string value) => ApplyFilters();
    partial void OnSelectedRegionFilterChanged(string value) => ApplyFilters();

    private void ApplyFilters()
    {
        FilteredVarietyQueue.Clear();
        foreach (var row in VarietyQueue)
        {
            if (!string.Equals(SelectedCropFilter, "All", StringComparison.OrdinalIgnoreCase))
            {
                if (!string.Equals(row.AmedCrop, SelectedCropFilter, StringComparison.OrdinalIgnoreCase))
                    continue;
            }
            // Region filter is currently a no-op because VarietyQueueRow doesn't
            // carry a region field yet. TODO: extend VarietyQueueRow to include
            // farmers.region once Mango Agent 1's column is wired through the
            // SupabaseService payload.
            FilteredVarietyQueue.Add(row);
        }
    }

    public AlertsViewModel() : this(App.Supabase) { }

    public AlertsViewModel(SupabaseService? supabase)
    {
        _supabase = supabase;
        SeedDesignTime();
        _ = LoadAsync();
    }

    private void SeedDesignTime()
    {
        NdviAlerts.Add(new NdviAlert
        {
            Id = Guid.NewGuid(),
            FarmerName = "Sample Farmer 03",
            Severity = "Moderate",
            Message = "NDVI declined 18% in last 7 days",
            CreatedAt = DateTime.UtcNow.AddDays(-1)
        });
        NdviAlertCount = NdviAlerts.Count;

        VarietySummary = new VarietyCollectionSummary
        {
            Complete = 46,
            AwaitingReply = 33,
            Abandoned = 9,
            AgentRequired = 14,
            Total = 102
        };
    }

    [RelayCommand]
    public async Task LoadAsync()
    {
        if (_supabase is null) return;

        CropMismatches.Clear();
        AreaMismatches.Clear();

        var rows = await _supabase.GetCropMismatches();
        foreach (var row in rows)
        {
            if (row.CropTypeMismatch)
                CropMismatches.Add(row);
            if (row.AreaDiffPct > 20)
                AreaMismatches.Add(row);
        }

        CropMismatchCount = CropMismatches.Count;
        AreaMismatchCount = AreaMismatches.Count;

        VarietySummary = await _supabase.GetVarietyCollectionStatusAsync();

        VarietyQueue.Clear();
        var queue = await _supabase.GetVarietyQueueAsync();
        foreach (var q in queue)
        {
            VarietyQueue.Add(q);
        }

        ApplyFilters();
    }

    [RelayCommand]
    private async Task SendReminderAsync(Guid farmerId)
    {
        if (_supabase is null) return;
        await _supabase.TriggerVarietyCollectionAsync(farmerId);
        await LoadAsync();
    }

    [RelayCommand]
    private async Task MarkAgentRequiredAsync(Guid farmerId)
    {
        // TODO: call SupabaseService.UpdateVarietyCollectionStatusAsync(farmerId,
        //   "AGENT_REQUIRED") once that method is added. For now we just refresh.
        _ = farmerId;
        await LoadAsync();
    }

    [RelayCommand]
    private void OpenManualEntry(VarietyQueueRow row)
    {
        SelectedQueueRow = row;
        ManualEntry = new ManualVarietyEntry();
        ManualFormVisible = true;
    }

    [RelayCommand]
    private async Task SaveManualAsync()
    {
        if (_supabase is null) return;
        if (SelectedQueueRow is null) return;

        var plotId = SelectedQueueRow.PlotId ?? Guid.Empty;
        await _supabase.SaveVarietyManuallyAsync(
            SelectedQueueRow.FarmerId,
            plotId,
            ManualEntry);

        ManualFormVisible = false;
        SelectedQueueRow = null;
        ManualEntry = new ManualVarietyEntry();
        await LoadAsync();
    }
}
