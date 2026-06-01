using System;
using System.Collections.ObjectModel;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using ShetMitraAdmin.Models;
using ShetMitraAdmin.Services;

namespace ShetMitraAdmin.ViewModels;

#nullable enable

public partial class FarmerDetailViewModel : ObservableObject
{
    private readonly SupabaseService? _supabase;

    [ObservableProperty] private string farmerName = "";
    [ObservableProperty] private string village = "";
    [ObservableProperty] private double registeredFieldSize;
    [ObservableProperty] private string registeredCrop = "";

    // Region badge (Bagaan Sathi SDD §7) — "MH" (ShetMitra) / "JH" (Bagaan
    // Sathi). Derived from <see cref="CropRegion"/> / <see cref="Village"/>
    // until the farmers.region column is wired through the API.
    [ObservableProperty] private string farmerRegion = "MH";

    public bool IsJharkhandFarmer => string.Equals(FarmerRegion, "JH", StringComparison.OrdinalIgnoreCase);
    public bool IsMaharashtraFarmer => !IsJharkhandFarmer;

    // AMED Intelligence section (SDD §7 Agent 6 Change 1)
    [ObservableProperty] private string? amedCropType;
    [ObservableProperty] private double amedConfidence;
    [ObservableProperty] private double amedFieldSize;
    [ObservableProperty] private bool cropMismatch;
    [ObservableProperty] private double areaMismatchPct;
    [ObservableProperty] private DateOnly? amedSowingDate;
    [ObservableProperty] private DateOnly? amedHarvestDate;
    [ObservableProperty] private string? amedGrowthStage;
    [ObservableProperty] private DateOnly? amedLastIrrigation;

    // Variety collection details (May 2026 spec)
    [ObservableProperty] private string? variety;
    [ObservableProperty] private string? varietySource;
    [ObservableProperty] private int brixTargetMin;
    [ObservableProperty] private int brixTargetMax;
    [ObservableProperty] private string varietyCollectionStatus = "PENDING";
    [ObservableProperty] private double revenuePotentialInr;
    [ObservableProperty] private bool isPremiumVariety;
    [ObservableProperty] private double premiumPct;

    // Mango intelligence (May 2026 — Mango Agent 8 §9)
    [ObservableProperty] private string? cropRegion;
    [ObservableProperty] private string? bearingYear;
    [ObservableProperty] private double? bearingConfidence;
    [ObservableProperty] private bool floweringDetected;
    [ObservableProperty] private bool fruitSetDetected;
    [ObservableProperty] private int? treeCount;
    [ObservableProperty] private int? treeAgeYears;
    [ObservableProperty] private bool isAlphonsoGiTagged;

    public bool IsMangoFarmer =>
        string.Equals(AmedCropType, "Mango", StringComparison.OrdinalIgnoreCase) ||
        string.Equals(RegisteredCrop, "Mango", StringComparison.OrdinalIgnoreCase);

    partial void OnAmedCropTypeChanged(string? value)
    {
        OnPropertyChanged(nameof(IsMangoFarmer));
        RecomputeGiTag();
    }

    partial void OnVarietyChanged(string? value) => RecomputeGiTag();
    partial void OnCropRegionChanged(string? value) => RecomputeGiTag();

    private void RecomputeGiTag()
    {
        // Konkan GI tag = Alphonso variety grown in Ratnagiri or Sindhudurg.
        var isAlphonso = string.Equals(Variety, "Alphonso", StringComparison.OrdinalIgnoreCase);
        var district = (CropRegion ?? Village ?? "").ToLowerInvariant();
        IsAlphonsoGiTagged = isAlphonso &&
                             (district.Contains("ratnagiri") || district.Contains("sindhudurg"));
    }

    public ObservableCollection<AmedHistoryRow> AmedHistory { get; } = new();

    public FarmerDetailViewModel() : this(App.Supabase) { }

    public FarmerDetailViewModel(SupabaseService? supabase)
    {
        _supabase = supabase;
        SeedDesignTimePreview();
    }

    private void SeedDesignTimePreview()
    {
        FarmerName = "Suresh Patil";
        Village = "Tasgaon, Sangli";
        RegisteredCrop = "Dry Grapes";
        RegisteredFieldSize = 2.0;

        AmedCropType = "Dry Grapes";
        AmedConfidence = 0.92;
        AmedFieldSize = 2.1;
        CropMismatch = false;
        AreaMismatchPct = 5;
        AmedSowingDate = new DateOnly(2025, 10, 18);
        AmedHarvestDate = new DateOnly(2026, 4, 14);
        AmedGrowthStage = "Veraison";
        AmedLastIrrigation = new DateOnly(2026, 5, 26);

        Variety = "Thompson Seedless";
        VarietySource = "farmer_reported";
        BrixTargetMin = 18;
        BrixTargetMax = 22;
        VarietyCollectionStatus = "COMPLETE";
        RevenuePotentialInr = 245000;
        IsPremiumVariety = true;
        PremiumPct = 15;

        // Design-time mango snapshot — hidden when crop is not Mango.
        CropRegion = "Ratnagiri";
        BearingYear = "ON";
        BearingConfidence = 0.78;
        FloweringDetected = true;
        FruitSetDetected = true;
        TreeCount = 240;
        TreeAgeYears = 12;

        AmedHistory.Add(new AmedHistoryRow
        {
            Season = "2022-23",
            Crop = "Dry Grapes",
            Sowing = new DateOnly(2022, 10, 14),
            Harvest = new DateOnly(2023, 4, 21),
            Acres = 2.0
        });
        AmedHistory.Add(new AmedHistoryRow
        {
            Season = "2023-24",
            Crop = "Dry Grapes",
            Sowing = new DateOnly(2023, 10, 16),
            Harvest = new DateOnly(2024, 4, 19),
            Acres = 2.0
        });
        AmedHistory.Add(new AmedHistoryRow
        {
            Season = "2024-25",
            Crop = "Dry Grapes",
            Sowing = new DateOnly(2024, 10, 17),
            Harvest = new DateOnly(2025, 4, 14),
            Acres = 2.1
        });
    }

    public async Task LoadAsync(Guid farmerId)
    {
        if (_supabase is null) return;

        var readings = await _supabase.GetAMEDReadings(farmerId);
        var latest = readings.FirstOrDefault();
        if (latest is null) return;

        AmedCropType = latest.CropType;
        AmedConfidence = latest.Confidence;
        AmedFieldSize = latest.FieldSizeAcres;
        AmedSowingDate = latest.SowingDate;
        AmedHarvestDate = latest.PredictedHarvestDate;
        AmedGrowthStage = latest.GrowthStage;
        AmedLastIrrigation = latest.LastIrrigationDate;

        CropMismatch = !string.Equals(AmedCropType, RegisteredCrop, StringComparison.OrdinalIgnoreCase);

        if (RegisteredFieldSize > 0)
        {
            AreaMismatchPct = Math.Abs(AmedFieldSize - RegisteredFieldSize) / RegisteredFieldSize * 100.0;
        }

        // Variety + Brix snapshot from farmers + farm_plots + variety_config.json.
        var varietyDetails = await _supabase.GetFarmerVarietyAsync(farmerId);
        if (varietyDetails is not null)
        {
            Variety = varietyDetails.Variety;
            VarietySource = varietyDetails.VarietySource;
            VarietyCollectionStatus = string.IsNullOrEmpty(varietyDetails.VarietyCollectionStatus)
                ? "PENDING"
                : varietyDetails.VarietyCollectionStatus;
            if (varietyDetails.BrixTargetMin is int min) BrixTargetMin = min;
            if (varietyDetails.BrixTargetMax is int max) BrixTargetMax = max;
            IsPremiumVariety = varietyDetails.IsPremiumVariety;
            if (varietyDetails.PremiumPct is double pct) PremiumPct = pct;
        }

        // Revenue potential is computed from yield+price when those are loaded;
        // until those fields are wired the existing XAML hides the row at 0.
        if (RevenuePotentialInr <= 0)
        {
            RevenuePotentialInr = 0;
        }

        // Mango intelligence (May 2026 — Mango Agent 8 §9.2). When the AMED
        // crop is Mango we populate bearing-year + phenology fields from the
        // mango_phenology_log. TODO: source plot_id + tree_count from the
        // farmer/farm_plots payload once Mango Agent 1 has added them.
        if (IsMangoFarmer)
        {
            try
            {
                var phenology = await _supabase.GetMangoPhenologyAsync(plotId: null);
                if (phenology.Count > 0)
                {
                    var phen = phenology[0];
                    BearingYear = phen.BearingYear;
                    BearingConfidence = 0.74; // TODO: source from phenology row when added
                    FloweringDetected = phen.FloweringStartDate.HasValue;
                    FruitSetDetected = phen.FruitSetDate.HasValue;
                }
            }
            catch
            {
                // best-effort — leave bearing fields null
            }

            // TODO: load tree_count / tree_age_years / region from farm_plots
            // once Mango Agent 1's schema additions land. Placeholder for now.
            if (TreeCount is null) TreeCount = 240;
            if (TreeAgeYears is null) TreeAgeYears = 12;
            if (string.IsNullOrEmpty(CropRegion)) CropRegion = Village;
            RecomputeGiTag();
        }
    }

    public bool IsAreaMismatch => AreaMismatchPct > 20;
    public bool IsAnyMismatch => CropMismatch || IsAreaMismatch;

    partial void OnAreaMismatchPctChanged(double value)
    {
        OnPropertyChanged(nameof(IsAreaMismatch));
        OnPropertyChanged(nameof(IsAnyMismatch));
    }

    partial void OnFarmerRegionChanged(string value)
    {
        OnPropertyChanged(nameof(IsJharkhandFarmer));
        OnPropertyChanged(nameof(IsMaharashtraFarmer));
    }

    partial void OnCropMismatchChanged(bool value)
    {
        OnPropertyChanged(nameof(IsAnyMismatch));
    }
}
