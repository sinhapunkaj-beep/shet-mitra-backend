# ShetMitra Admin (WPF)

WPF admin dashboard for the ShetMitra / Sahyadri Krushi Intelligence platform.
Implements the AMED Intelligence surfaces defined in SDD Section 7 Agent 6.

This is a fresh scaffolding produced by Agent 6 of the parallel build —
no prior `admin_wpf/` directory existed in the repository.

## Build

```
dotnet restore admin_wpf/ShetMitraAdmin.csproj
dotnet build   admin_wpf/ShetMitraAdmin.csproj
```

NuGet restore is required before the first build will succeed. The skeleton
references the following packages:

- `CommunityToolkit.Mvvm` 8.x — source-generated `[ObservableProperty]` and `[RelayCommand]`
- `LiveChartsCore.SkiaSharpView.WPF` 2.0.0-rc4 — bar / stacked-column charts
- `supabase-csharp` 0.16.x — PostgREST client for Supabase
- `Microsoft.Extensions.Configuration.Json` — reads `appsettings.json`

## Configuration

`appsettings.json` holds the Supabase URL and anon key:

```
"Supabase": {
  "Url":     "https://euydubpywdsettjywkms.supabase.co",
  "AnonKey": "<TEST project anon JWT>"
}
```

The anon key embedded in `appsettings.json` is the public ShetMitra TEST
project anon JWT — safe to commit (it is already public in the production
PWA HTML). The service-role key and DB password must never be embedded in
this file; they live in user-private env files.

## Sidebar

Six items in order:

1. Dashboard
2. Farmers
3. Alerts
4. Analytics
5. Belt Intelligence  (NEW — inserted between Analytics and Drone Operations per SDD Change 3)
6. Drone Operations

## Views

- `Views/DashboardView.xaml` — existing dashboard cards plus the
  "Tasgaon Belt — This Week" belt-intelligence card (estimated arrivals MT,
  fields harvesting, belt health good %, price-pressure indicator).
- `Views/FarmerDetailView.xaml` — adds an "AMED Intelligence" section showing
  AMED crop type + confidence badge, AMED field size vs registered, mismatch
  warning text (red border when crop or area mismatch is flagged), sowing date,
  predicted harvest, growth stage, last irrigation, and the 3-year crop history
  table.
- `Views/AlertsView.xaml` — adds an "AMED Mismatches" tab with two sub-lists
  (crop mismatch alerts and area mismatch alerts, each with a count badge)
  alongside the existing NDVI deterioration alerts.
- `Views/BeltIntelligenceView.xaml` — NEW screen with the top cards (total
  fields 2847, total acres 8234, this-week / next-week arrivals, belt health
  good %, price pressure), a 4-week LiveCharts2 column chart of AMED forecast
  volume overlaid with a dashed historical-average line, a stacked column chart
  for belt health distribution per week, the historical comparison DataGrid
  (2022-23, 2023-24, 2024-25 + 2025-26 forecast), the mismatch report
  DataGrid, and a data-refresh info row with a manual refresh button.

## Services

`Services/SupabaseService.cs` provides:

- `GetAMEDReadings(Guid farmerId)` -> `amed_readings`
- `GetBeltData(string region, int weeks)` -> `amed_belt_data`
- `GetAMEDHistory(string region)` -> `amed_history`
- `GetCropMismatches()` -> `farm_plots` where mismatch flags are set

Each method currently returns deterministic placeholder data so the UI can be
exercised without a live database. Replace the bodies with `supabase.From<T>()`
calls once `supabase-csharp` is restored.

## Notes for the integration agent

- `dotnet restore` is required first — the package versions in the csproj are
  reasonable defaults but `LiveChartsCore.SkiaSharpView.WPF` may need to be
  bumped to whatever rc / final the team standardizes on.
- All XAML bindings use design-time `d:DataContext` for the WPF designer.
- File-scoped namespaces, `#nullable enable` everywhere.

## Variety Collection (May 2026)

The WPF dashboard now surfaces the WhatsApp variety-collection flow that runs
after the AMED pipeline detects a crop on a farmer's plot.

### New surfaces

- **Dashboard** — adds a "Variety Collection" card to row 2 showing the
  collected / total ratio, a sub-line with the "awaiting reply" and
  "need agent visit" counts, a progress bar, and an orange badge
  ("Agent visit needed: N") when at least one farmer is in
  `AGENT_REQUIRED`.
- **Alerts** — adds a "Variety Collection" tab with four summary cards
  (Collected = green, Awaiting reply = orange, Abandoned = grey,
  Agent required = red), a queue DataGrid with status badges and three
  per-row actions (`Send Reminder`, `Mark Agent Required`,
  `Enter Manually`), and an inline manual-entry form (Variety / Acres /
  Notes + Save) bound to `ManualEntry` / `SaveManualCommand`.
- **Farmer Detail** — extends the "AMED Intelligence" card with four new
  rows: variety + source badge, brix target (min–max), collection-status
  badge, and revenue potential (with a "(+N% premium)" suffix when the
  variety is mandi-grade premium).

### New models

- `Models/VarietyCollectionSummary.cs` — aggregate counts + computed
  `CompletionPct`.
- `Models/VarietyQueueRow.cs` — one row of the variety queue.
- `Models/ManualVarietyEntry.cs` — `ObservableObject` for the inline form.

### New converter

- `Converters/VarietyStatusToBrushConverter.cs` — maps status labels
  (`COMPLETE` / `AWAITING_REPLY` / `AGENT_REQUIRED` / `ABANDONED`) and
  `variety_source` labels (`agent_verified` / `farmer_reported` /
  `amed_hint`) to badge brushes. Registered in `App.xaml` as
  `VarietyStatusToBrushConverter`. A `BooleanToVisibilityConverter` is
  also registered globally as `BooleanToVisibilityConverter`.

### New SupabaseService methods

- `GetVarietyCollectionStatusAsync()` — returns a
  `VarietyCollectionSummary` (currently placeholder counts).
- `GetVarietyQueueAsync()` — returns sample `VarietyQueueRow` items
  covering each status (placeholder).
- `TriggerVarietyCollectionAsync(Guid farmerId)` — POSTs to
  `{Internal:BaseUrl}/internal/trigger-variety-collection`. Returns
  `true` on HTTP 200, `false` otherwise (exceptions are caught and
  logged via `Debug.WriteLine`).
- `SaveVarietyManuallyAsync(Guid farmerId, Guid plotId, ManualVarietyEntry entry)` —
  placeholder that returns `true`. TODO comment shows the two UPDATE +
  one INSERT chain per spec.

### Configuration

`appsettings.json` has a new section:

```
"Internal": {
  "BaseUrl": "http://localhost:8000"
}
```

This controls where `TriggerVarietyCollectionAsync` POSTs. Point it at
the production FastAPI host when deploying.

## Mango Crop Expansion (May 2026)

Mango Agent 8 of the parallel build adds the dashboard surfaces for the
Mango crop expansion (Alphonso / Kesar / Dasheri / Totapuri / Banganapalli)
defined in the Mango SDD §9.

### New views

- **Price Analysis** (`Views/PriceAnalysisView.xaml`) — new sidebar entry
  between Analytics and Belt Intelligence. Commodity dropdown (Dry Grapes
  / Pomegranate / Mango); when Mango is selected a Variety dropdown is
  revealed and the Mandi dropdown is repopulated per variety:
  Alphonso → Ratnagiri / Devgad / Vashi, Kesar → Aurangabad / Nashik /
  Pune, Dasheri → Nagpur / Amravati, Totapuri → Latur / Solapur,
  Banganapalli → Sangli / Kolhapur. A LiveCharts2 line chart shows price
  history with **ON / OFF bearing-year band overlays** (light blue / light
  orange via `BearingToBrushConverter`). The series is synthetic
  placeholder data — TODO wire to the real mandi_prices feed.
- **Belt Intelligence** — three new region tabs added after the existing
  Tasgaon (Grapes) tab: `Konkan Belt (Alphonso)`, `Nashik Belt (Kesar)`,
  `Vidarbha Belt (Dasheri)`. Each tab has KPI cards (total fields, acres,
  this-week MT, bearing-year badge tinted via `BearingToBrushConverter`),
  a volume forecast column chart, and a stacked health distribution
  chart.
- **Farmer Detail** — a new yellow-tinted **Mango Intelligence**
  sub-section appended to the AMED Intelligence card. Visible only when
  the AMED or registered crop is "Mango". Shows the bearing-year badge
  with confidence, flowering / fruit-set flags, tree count + age, plus a
  **Konkan GI** chip when variety is Alphonso and the district contains
  Ratnagiri / Sindhudurg.
- **Dashboard** — a new Mango stat card showing total Mango farms, the
  bearing-year badge for the current season, and the this-week belt
  volume in MT.
- **Alerts** — Variety Collection tab gains Crop (All / Dry Grapes /
  Pomegranate / Mango) and Region (All / Konkan / Marathwada / Vidarbha
  / Other) filter dropdowns. Filtering is currently client-side.

### New models

- `Models/MangoPhenologyRow.cs` — one row of `mango_phenology_log`
  (plot, season, bearing year, flowering / fruit-set dates, stress
  counts, predicted yield).
- `Models/MangoBeltRow.cs` — one row of `mango_belt_data` (per-region
  per-variety weekly aggregate).
- `Models/BearingYearAnnotation.cs` — annotation record for ON / OFF
  band overlays on the price chart.

### New converter

- `Converters/BearingToBrushConverter.cs` — maps `ON` → light blue
  `#90CAF9`, `OFF` → light orange `#FFB74D`, `UNKNOWN` → light grey
  `#E0E0E0`. Registered in `App.xaml` as `BearingToBrushConverter`.

### New SupabaseService methods

- `GetMangoPhenologyAsync(Guid? plotId = null)` — GET
  `/rest/v1/mango_phenology_log` (optional `plot_id=eq.` filter). Falls
  back to placeholder rows.
- `GetMangoBeltDataAsync(string region, string? variety = null, int limit = 30)` —
  GET `/rest/v1/mango_belt_data?region=eq.{r}&variety=eq.{v}&order=fetch_date.desc&limit={n}`.
  Falls back to deterministic placeholder rows.

Both methods are additive; the existing service methods are untouched.

### Follow-up TODOs

- Wire real `mandi_prices` history into the Price Analysis chart series
  (currently synthetic).
- Source `tree_count` / `tree_age_years` / `region` on `FarmerDetailViewModel`
  from `farm_plots` once Mango Agent 1's schema additions ship.
- Extend `VarietyQueueRow` with the new `farmers.region` column so the
  Alerts region filter can actually narrow the queue.
- Replace placeholder MangoPhenology / MangoBelt rows once Mango Agent 1
  provisions the new Supabase tables.
