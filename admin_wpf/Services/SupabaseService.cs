using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Tasks;
using ShetMitraAdmin.Models;

namespace ShetMitraAdmin.Services;

#nullable enable

/// <summary>
/// Thin wrapper around supabase-csharp. When the NuGet package is restored,
/// the inner client will be initialized and the methods will hit the real
/// PostgREST endpoints. Until then, deterministic placeholder data is
/// returned so that the UI can be reviewed without a live database.
///
/// Tables (per SDD Section 3):
///   amed_readings, amed_belt_data, amed_history, farm_plots (with
///   crop_type_mismatch / area_mismatch_pct), farmers, variety_responses.
/// </summary>
public sealed class SupabaseService
{
    private readonly string _url;
    private readonly string _anonKey;
    private readonly string _internalBaseUrl;
    private static readonly HttpClient HttpClient = new();

    public SupabaseService(string url, string anonKey)
        : this(url, anonKey, "http://localhost:8000") { }

    public SupabaseService(string url, string anonKey, string internalBaseUrl)
    {
        _url = url;
        _anonKey = anonKey;
        _internalBaseUrl = string.IsNullOrWhiteSpace(internalBaseUrl)
            ? "http://localhost:8000"
            : internalBaseUrl.TrimEnd('/');
    }

    public string SupabaseUrl => _url;
    public string InternalBaseUrl => _internalBaseUrl;

    public Task<List<AmedReading>> GetAMEDReadings(Guid farmerId)
    {
        // TODO: replace with supabase.From<AmedReading>().Where(r => r.FarmerId == farmerId).Get();
        var data = new List<AmedReading>
        {
            new()
            {
                Id = Guid.NewGuid(),
                FarmerId = farmerId,
                CropType = "Dry Grapes",
                Confidence = 0.92,
                FieldSizeAcres = 2.1,
                SowingDate = new DateOnly(2025, 10, 18),
                PredictedHarvestDate = new DateOnly(2026, 4, 14),
                GrowthStage = "Veraison",
                LastIrrigationDate = new DateOnly(2026, 5, 26),
                FetchDate = DateOnly.FromDateTime(DateTime.UtcNow)
            }
        };
        return Task.FromResult(data);
    }

    public Task<List<AmedBeltData>> GetBeltData(string region, int weeks)
    {
        // TODO: supabase.From<AmedBeltData>()
        //   .Where(b => b.Region == region)
        //   .Order(b => b.WeekStart, Ordering.Descending)
        //   .Limit(weeks)
        //   .Get();
        var today = DateOnly.FromDateTime(DateTime.UtcNow);
        var data = new List<AmedBeltData>();
        var rng = new Random(42);
        for (var i = 0; i < weeks; i++)
        {
            data.Add(new AmedBeltData
            {
                Id = Guid.NewGuid(),
                Region = region,
                WeekStart = today.AddDays(7 * i),
                ForecastVolumeMt = 520 + rng.Next(0, 200),
                FieldsHarvesting = 380 + rng.Next(0, 150),
                HealthGoodPct = 58 + rng.NextDouble() * 12,
                HealthModeratePct = 22 + rng.NextDouble() * 6,
                HealthStressedPct = 10 + rng.NextDouble() * 4,
                HealthCriticalPct = 4 + rng.NextDouble() * 3,
                PricePressure = i switch
                {
                    0 => "HIGH",
                    1 => "MEDIUM",
                    _ => "LOW"
                },
                FetchDate = today
            });
        }
        return Task.FromResult(data);
    }

    public Task<List<AmedHistory>> GetAMEDHistory(string region)
    {
        // Seed numbers per SDD Section 7 Agent 6 Change "NEW SCREEN":
        //   2022-23 | Apr 21 | 680 MT | 118/kg
        //   2023-24 | Apr 19 | 695 MT | 134/kg
        //   2024-25 | Apr 14 | 820 MT | 298/kg
        //   2025-26 | Apr 14 | 680 MT (forecast) | TBD
        var data = new List<AmedHistory>
        {
            new()
            {
                Id = Guid.NewGuid(),
                Region = region,
                SeasonLabel = "2022-23",
                SeasonYearStart = 2022,
                CropType = "Dry Grapes",
                TotalAreaAcres = 8234,
                HarvestPeakDate = new DateOnly(2023, 4, 21),
                EstimatedTotalVolumeMt = 11420,
                AvgPriceModalKg = 118
            },
            new()
            {
                Id = Guid.NewGuid(),
                Region = region,
                SeasonLabel = "2023-24",
                SeasonYearStart = 2023,
                CropType = "Dry Grapes",
                TotalAreaAcres = 8234,
                HarvestPeakDate = new DateOnly(2024, 4, 19),
                EstimatedTotalVolumeMt = 11680,
                AvgPriceModalKg = 134
            },
            new()
            {
                Id = Guid.NewGuid(),
                Region = region,
                SeasonLabel = "2024-25",
                SeasonYearStart = 2024,
                CropType = "Dry Grapes",
                TotalAreaAcres = 8234,
                HarvestPeakDate = new DateOnly(2025, 4, 14),
                EstimatedTotalVolumeMt = 11890,
                AvgPriceModalKg = 298
            },
            new()
            {
                Id = Guid.NewGuid(),
                Region = region,
                SeasonLabel = "2025-26 (forecast)",
                SeasonYearStart = 2025,
                CropType = "Dry Grapes",
                TotalAreaAcres = 8234,
                HarvestPeakDate = new DateOnly(2026, 4, 14),
                EstimatedTotalVolumeMt = 680,
                AvgPriceModalKg = null
            }
        };
        return Task.FromResult(data);
    }

    public Task<List<CropMismatch>> GetCropMismatches()
    {
        // TODO: supabase.From<FarmPlot>()
        //   .Where(p => p.CropTypeMismatch == true || p.AreaMismatchPct > 20)
        //   .Get();
        var data = new List<CropMismatch>
        {
            new()
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
            },
            new()
            {
                FarmerId = Guid.NewGuid(),
                FarmerName = "Sample Farmer 47",
                RegisteredCrop = "Dry Grapes",
                AmedDetectedCrop = "Dry Grapes",
                RegisteredAcres = 3.0,
                AmedAcres = 3.8,
                AreaDiffPct = 26.6,
                CropTypeMismatch = false,
                RecommendedAction = "Update registered acres"
            }
        };
        return Task.FromResult(data);
    }

    // ─── Variety collection (May 2026) ────────────────────────────────

    /// <summary>
    /// Returns aggregate counts by <c>farmers.variety_collection_status</c>.
    /// PostgREST v1 has no GROUP BY, so the four buckets + the total are
    /// fetched in parallel with <c>Prefer: count=exact</c> and the count is
    /// parsed from the <c>Content-Range</c> header. On any failure we fall
    /// back to the previous placeholder numbers so the UI still renders.
    /// </summary>
    public async Task<VarietyCollectionSummary> GetVarietyCollectionStatusAsync()
    {
        try
        {
            var completeTask = CountFarmersAsync("variety_collection_status=eq.COMPLETE");
            var awaitingTask = CountFarmersAsync("variety_collection_status=eq.AWAITING_REPLY");
            var abandonedTask = CountFarmersAsync("variety_collection_status=eq.ABANDONED");
            var agentTask = CountFarmersAsync("variety_collection_status=eq.AGENT_REQUIRED");
            var totalTask = CountFarmersAsync(null);

            await Task.WhenAll(completeTask, awaitingTask, abandonedTask, agentTask, totalTask);

            return new VarietyCollectionSummary
            {
                Complete = completeTask.Result,
                AwaitingReply = awaitingTask.Result,
                Abandoned = abandonedTask.Result,
                AgentRequired = agentTask.Result,
                Total = totalTask.Result
            };
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"GetVarietyCollectionStatusAsync failed, returning placeholder: {ex.Message}");
            return new VarietyCollectionSummary
            {
                Complete = 46,
                AwaitingReply = 33,
                Abandoned = 9,
                AgentRequired = 14,
                Total = 102
            };
        }
    }

    private async Task<int> CountFarmersAsync(string? filterQuery)
    {
        var url = string.IsNullOrEmpty(filterQuery)
            ? $"{_url.TrimEnd('/')}/rest/v1/farmers?select=id"
            : $"{_url.TrimEnd('/')}/rest/v1/farmers?select=id&{filterQuery}";

        using var request = new HttpRequestMessage(HttpMethod.Get, url);
        ApplySupabaseHeaders(request);
        request.Headers.TryAddWithoutValidation("Prefer", "count=exact");
        request.Headers.Range = new RangeHeaderValue(0, 0);

        using var response = await HttpClient.SendAsync(request);
        if (response.Content.Headers.ContentRange is { } contentRange &&
            contentRange.HasLength &&
            contentRange.Length is long total)
        {
            return (int)total;
        }

        // Fallback: re-fetch without Range and count rows.
        using var fullRequest = new HttpRequestMessage(HttpMethod.Get, url);
        ApplySupabaseHeaders(fullRequest);
        using var fullResponse = await HttpClient.SendAsync(fullRequest);
        fullResponse.EnsureSuccessStatusCode();
        var json = await fullResponse.Content.ReadAsStringAsync();
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.ValueKind == JsonValueKind.Array
            ? doc.RootElement.GetArrayLength()
            : 0;
    }

    private void ApplySupabaseHeaders(HttpRequestMessage request)
    {
        request.Headers.TryAddWithoutValidation("apikey", _anonKey);
        request.Headers.TryAddWithoutValidation("Authorization", $"Bearer {_anonKey}");
        request.Headers.TryAddWithoutValidation("Accept", "application/json");
    }

    /// <summary>
    /// Returns the variety-collection queue for the Alerts screen — one row
    /// per farmer with the latest <c>farm_plots</c> snapshot embedded. The
    /// PostgREST query uses the foreign-key inferred embedding
    /// <c>farm_plots(id)</c> to fetch the first plot id per farmer.
    /// </summary>
    public async Task<List<VarietyQueueRow>> GetVarietyQueueAsync()
    {
        var url =
            $"{_url.TrimEnd('/')}/rest/v1/farmers" +
            "?select=id,farmer_full_name,mobile_number,current_crop," +
            "variety_collection_status,variety_collection_attempts," +
            "variety_collection_attempted_at,farm_plots(id)" +
            "&amed_variety_collected=eq.false" +
            "&order=variety_collection_attempts.desc" +
            "&limit=200";

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            ApplySupabaseHeaders(request);
            using var response = await HttpClient.SendAsync(request);
            response.EnsureSuccessStatusCode();
            var json = await response.Content.ReadAsStringAsync();
            var dtos = JsonSerializer.Deserialize<List<VarietyQueueRowDto>>(json, JsonOpts)
                       ?? new List<VarietyQueueRowDto>();

            return dtos.Select(d => new VarietyQueueRow
            {
                FarmerId = d.Id ?? Guid.Empty,
                PlotId = d.FarmPlots is { Count: > 0 } ? d.FarmPlots[0].Id : null,
                FarmerName = d.FarmerFullName ?? "",
                Mobile = d.MobileNumber ?? "",
                AmedCrop = d.CurrentCrop ?? "",
                Status = string.IsNullOrEmpty(d.VarietyCollectionStatus) ? "PENDING" : d.VarietyCollectionStatus,
                Attempts = d.VarietyCollectionAttempts ?? 0,
                LastAttempt = d.VarietyCollectionAttemptedAt
            }).ToList();
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"GetVarietyQueueAsync failed, returning placeholder: {ex.Message}");
            var now = DateTime.UtcNow;
            return new List<VarietyQueueRow>
            {
                new()
                {
                    FarmerId = Guid.NewGuid(),
                    PlotId = Guid.NewGuid(),
                    FarmerName = "Suresh Patil",
                    Mobile = "9876543210",
                    AmedCrop = "Dry Grapes",
                    Status = "AWAITING_REPLY",
                    Attempts = 1,
                    LastAttempt = now.AddHours(-6)
                },
                new()
                {
                    FarmerId = Guid.NewGuid(),
                    PlotId = Guid.NewGuid(),
                    FarmerName = "Ramesh Jadhav",
                    Mobile = "9123456789",
                    AmedCrop = "Pomegranate",
                    Status = "AGENT_REQUIRED",
                    Attempts = 3,
                    LastAttempt = now.AddHours(-30)
                },
                new()
                {
                    FarmerId = Guid.NewGuid(),
                    PlotId = Guid.NewGuid(),
                    FarmerName = "Mahesh Kale",
                    Mobile = "9988776655",
                    AmedCrop = "Mango",
                    Status = "COMPLETE",
                    Attempts = 1,
                    LastAttempt = now.AddDays(-2)
                },
                new()
                {
                    FarmerId = Guid.NewGuid(),
                    PlotId = Guid.NewGuid(),
                    FarmerName = "Vijay Shinde",
                    Mobile = "9112233445",
                    AmedCrop = "Dry Grapes",
                    Status = "ABANDONED",
                    Attempts = 3,
                    LastAttempt = now.AddDays(-4)
                },
                new()
                {
                    FarmerId = Guid.NewGuid(),
                    PlotId = Guid.NewGuid(),
                    FarmerName = "Anil Deshmukh",
                    Mobile = "9001234567",
                    AmedCrop = "Pomegranate",
                    Status = "AWAITING_REPLY",
                    Attempts = 2,
                    LastAttempt = now.AddHours(-18)
                }
            };
        }
    }

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true
    };

    private sealed class VarietyQueueRowDto
    {
        [JsonPropertyName("id")] public Guid? Id { get; set; }
        [JsonPropertyName("farmer_full_name")] public string? FarmerFullName { get; set; }
        [JsonPropertyName("mobile_number")] public string? MobileNumber { get; set; }
        [JsonPropertyName("current_crop")] public string? CurrentCrop { get; set; }
        [JsonPropertyName("variety_collection_status")] public string? VarietyCollectionStatus { get; set; }
        [JsonPropertyName("variety_collection_attempts")] public int? VarietyCollectionAttempts { get; set; }
        [JsonPropertyName("variety_collection_attempted_at")] public DateTime? VarietyCollectionAttemptedAt { get; set; }
        [JsonPropertyName("farm_plots")] public List<FarmPlotIdDto>? FarmPlots { get; set; }
    }

    private sealed class FarmPlotIdDto
    {
        [JsonPropertyName("id")] public Guid? Id { get; set; }
    }

    /// <summary>
    /// POSTs to <c>{InternalBaseUrl}/internal/trigger-variety-collection</c>
    /// to re-send the variety-collection WhatsApp message for a farmer.
    /// </summary>
    public async Task<bool> TriggerVarietyCollectionAsync(Guid farmerId)
    {
        var url = $"{_internalBaseUrl}/internal/trigger-variety-collection";
        try
        {
            using var response = await HttpClient.PostAsJsonAsync(
                url,
                new { farmer_id = farmerId });
            return response.IsSuccessStatusCode;
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"TriggerVarietyCollectionAsync failed: {ex.Message}");
            return false;
        }
    }

    /// <summary>
    /// Persists a manual variety entry — performed by an agent when the
    /// farmer cannot or did not respond on WhatsApp. Runs three sequential
    /// PostgREST calls: <c>PATCH farm_plots</c>, <c>PATCH farmers</c>,
    /// <c>POST variety_responses</c>. No rollback is attempted — a partial
    /// failure must be reconciled manually via the Supabase dashboard.
    /// </summary>
    public async Task<bool> SaveVarietyManuallyAsync(Guid farmerId, Guid plotId, ManualVarietyEntry entry)
    {
        var nowIso = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ");
        var baseUrl = _url.TrimEnd('/');

        try
        {
            // 1. PATCH farm_plots
            var plotBody = JsonSerializer.Serialize(new
            {
                current_crop_variety = entry.Variety,
                self_reported_acres = entry.Acres,
                variety_source = "agent_verified"
            });
            using (var patchPlot = new HttpRequestMessage(
                       new HttpMethod("PATCH"),
                       $"{baseUrl}/rest/v1/farm_plots?id=eq.{plotId}"))
            {
                ApplySupabaseHeaders(patchPlot);
                patchPlot.Headers.TryAddWithoutValidation("Prefer", "return=minimal");
                patchPlot.Content = new StringContent(plotBody, Encoding.UTF8, "application/json");
                using var resp = await HttpClient.SendAsync(patchPlot);
                if (!resp.IsSuccessStatusCode)
                {
                    var body = await SafeReadBodyAsync(resp);
                    Debug.WriteLine(
                        $"SaveVarietyManuallyAsync PATCH farm_plots failed: status={(int)resp.StatusCode} body={body}");
                    return false;
                }
            }

            // 2. PATCH farmers
            var farmerBody = JsonSerializer.Serialize(new
            {
                amed_variety_collected = true,
                amed_variety_collected_at = nowIso,
                variety_collection_status = "COMPLETE"
            });
            using (var patchFarmer = new HttpRequestMessage(
                       new HttpMethod("PATCH"),
                       $"{baseUrl}/rest/v1/farmers?id=eq.{farmerId}"))
            {
                ApplySupabaseHeaders(patchFarmer);
                patchFarmer.Headers.TryAddWithoutValidation("Prefer", "return=minimal");
                patchFarmer.Content = new StringContent(farmerBody, Encoding.UTF8, "application/json");
                using var resp = await HttpClient.SendAsync(patchFarmer);
                if (!resp.IsSuccessStatusCode)
                {
                    var body = await SafeReadBodyAsync(resp);
                    Debug.WriteLine(
                        $"SaveVarietyManuallyAsync PATCH farmers failed: status={(int)resp.StatusCode} body={body}");
                    return false;
                }
            }

            // 3. POST variety_responses
            var responseBody = JsonSerializer.Serialize(new
            {
                farmer_id = farmerId,
                plot_id = plotId,
                variety_reported = entry.Variety,
                acres_reported = entry.Acres,
                status = "COMPLETE",
                mismatch_resolution = "agent_manual_entry",
                collection_completed_at = nowIso
            });
            using (var postResp = new HttpRequestMessage(
                       HttpMethod.Post,
                       $"{baseUrl}/rest/v1/variety_responses"))
            {
                ApplySupabaseHeaders(postResp);
                postResp.Headers.TryAddWithoutValidation("Prefer", "return=minimal");
                postResp.Content = new StringContent(responseBody, Encoding.UTF8, "application/json");
                using var resp = await HttpClient.SendAsync(postResp);
                if (!resp.IsSuccessStatusCode)
                {
                    var body = await SafeReadBodyAsync(resp);
                    Debug.WriteLine(
                        $"SaveVarietyManuallyAsync POST variety_responses failed: status={(int)resp.StatusCode} body={body}");
                    return false;
                }
            }

            return true;
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"SaveVarietyManuallyAsync threw: {ex.Message}");
            return false;
        }
    }

    private static async Task<string> SafeReadBodyAsync(HttpResponseMessage response)
    {
        try
        {
            var body = await response.Content.ReadAsStringAsync();
            return body.Length > 400 ? body[..400] : body;
        }
        catch
        {
            return "<unreadable>";
        }
    }

    /// <summary>
    /// Returns the farmer's variety + plot snapshot enriched with Brix /
    /// premium metadata from <c>data/variety_config.json</c>. Returns
    /// <c>null</c> when the farmer row is not found.
    /// </summary>
    public async Task<FarmerVarietyDetails?> GetFarmerVarietyAsync(Guid farmerId)
    {
        var url =
            $"{_url.TrimEnd('/')}/rest/v1/farmers" +
            "?select=variety_collection_status,farm_plots(id,current_crop,current_crop_variety,variety_source)" +
            $"&id=eq.{farmerId}&limit=1";

        FarmerVarietyDto? farmerDto = null;
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            ApplySupabaseHeaders(request);
            using var response = await HttpClient.SendAsync(request);
            response.EnsureSuccessStatusCode();
            var json = await response.Content.ReadAsStringAsync();
            var list = JsonSerializer.Deserialize<List<FarmerVarietyDto>>(json, JsonOpts);
            if (list is null || list.Count == 0) return null;
            farmerDto = list[0];
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"GetFarmerVarietyAsync HTTP failed: {ex.Message}");
            return null;
        }

        var plot = farmerDto.FarmPlots is { Count: > 0 } ? farmerDto.FarmPlots[0] : null;
        var variety = plot?.CurrentCropVariety;
        var crop = plot?.CurrentCrop;
        var status = string.IsNullOrEmpty(farmerDto.VarietyCollectionStatus)
            ? "PENDING"
            : farmerDto.VarietyCollectionStatus;
        var source = plot?.VarietySource;

        // Enrich from variety_config.json (best effort).
        int? brixMin = null;
        int? brixMax = null;
        bool isPremium = false;
        double? premiumPct = null;
        try
        {
            var configPath = Path.Combine(AppContext.BaseDirectory, "data", "variety_config.json");
            if (File.Exists(configPath) && !string.IsNullOrEmpty(crop))
            {
                using var stream = File.OpenRead(configPath);
                using var doc = JsonDocument.Parse(stream);
                if (doc.RootElement.TryGetProperty(crop, out var cropEl))
                {
                    JsonElement varietyEl = default;
                    var found = false;
                    if (!string.IsNullOrEmpty(variety) &&
                        cropEl.TryGetProperty(variety, out varietyEl))
                    {
                        found = true;
                    }
                    else if (cropEl.TryGetProperty("default", out varietyEl))
                    {
                        found = true;
                    }

                    if (found)
                    {
                        if (varietyEl.TryGetProperty("brix_target_min", out var minEl) &&
                            minEl.TryGetInt32(out var min))
                        {
                            brixMin = min;
                        }
                        if (varietyEl.TryGetProperty("brix_target_max", out var maxEl) &&
                            maxEl.TryGetInt32(out var max))
                        {
                            brixMax = max;
                        }
                        if (varietyEl.TryGetProperty("mandi_grade_premium", out var premEl) &&
                            premEl.ValueKind is JsonValueKind.True or JsonValueKind.False)
                        {
                            isPremium = premEl.GetBoolean();
                        }
                        if (varietyEl.TryGetProperty("price_premium_pct", out var pctEl) &&
                            pctEl.TryGetDouble(out var pct))
                        {
                            premiumPct = pct;
                        }
                    }
                }
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"GetFarmerVarietyAsync variety_config load failed: {ex.Message}");
        }

        return new FarmerVarietyDetails
        {
            Variety = variety,
            VarietySource = source,
            VarietyCollectionStatus = status,
            BrixTargetMin = brixMin,
            BrixTargetMax = brixMax,
            IsPremiumVariety = isPremium,
            PremiumPct = premiumPct
        };
    }

    private sealed class FarmerVarietyDto
    {
        [JsonPropertyName("variety_collection_status")] public string? VarietyCollectionStatus { get; set; }
        [JsonPropertyName("farm_plots")] public List<FarmPlotVarietyDto>? FarmPlots { get; set; }
    }

    private sealed class FarmPlotVarietyDto
    {
        [JsonPropertyName("id")] public Guid? Id { get; set; }
        [JsonPropertyName("current_crop")] public string? CurrentCrop { get; set; }
        [JsonPropertyName("current_crop_variety")] public string? CurrentCropVariety { get; set; }
        [JsonPropertyName("variety_source")] public string? VarietySource { get; set; }
    }

    // ─── Trader Intelligence (May 2026) ───────────────────────────────

    /// <summary>
    /// Fetches the subscriber list for the Trader Intelligence Subscribers
    /// tab. Optional <paramref name="tierFilter"/> /
    /// <paramref name="statusFilter"/> are pushed down as PostgREST
    /// <c>eq</c> filters; the special sentinel <c>"ALL"</c> is treated as
    /// "no filter". Falls back to deterministic placeholder rows on any
    /// transport error so the DataGrid still renders.
    /// </summary>
    public async Task<List<TraderRow>> GetTradersAsync(
        string? tierFilter = null,
        string? statusFilter = null,
        int limit = 200)
    {
        var baseUrl = _url.TrimEnd('/');
        var sb = new StringBuilder();
        sb.Append(baseUrl);
        sb.Append("/rest/v1/traders");
        sb.Append("?select=id,full_name,mobile,business_name,district,");
        sb.Append("subscription_tier,subscription_status,subscription_started_at,");
        sb.Append("monthly_amount,query_count_this_month");
        sb.Append("&limit=").Append(limit);
        sb.Append("&order=created_at.desc");

        if (!string.IsNullOrEmpty(tierFilter) &&
            !string.Equals(tierFilter, "ALL", StringComparison.OrdinalIgnoreCase))
        {
            sb.Append("&subscription_tier=eq.")
              .Append(Uri.EscapeDataString(tierFilter));
        }
        if (!string.IsNullOrEmpty(statusFilter) &&
            !string.Equals(statusFilter, "ALL", StringComparison.OrdinalIgnoreCase))
        {
            sb.Append("&subscription_status=eq.")
              .Append(Uri.EscapeDataString(statusFilter));
        }

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, sb.ToString());
            ApplySupabaseHeaders(request);
            using var response = await HttpClient.SendAsync(request);
            response.EnsureSuccessStatusCode();
            var json = await response.Content.ReadAsStringAsync();
            return JsonSerializer.Deserialize<List<TraderRow>>(json, JsonOpts)
                   ?? new List<TraderRow>();
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"GetTradersAsync failed, returning placeholder: {ex.Message}");
            var now = DateTime.UtcNow;
            return new List<TraderRow>
            {
                new()
                {
                    Id = Guid.NewGuid(),
                    FullName = "Ravi Shah",
                    Mobile = "9876501234",
                    BusinessName = "Shah Commission Agents",
                    District = "Sangli",
                    Tier = "PREMIUM",
                    Status = "ACTIVE",
                    SubscriptionStartedAt = now.AddDays(-120),
                    MonthlyAmount = 15000,
                    QueryCountThisMonth = 18,
                    LastReportAt = now.AddDays(-2)
                },
                new()
                {
                    Id = Guid.NewGuid(),
                    FullName = "Anil Bhandari",
                    Mobile = "9123450098",
                    BusinessName = "Bhandari Raisin Exports",
                    District = "Nashik",
                    Tier = "STANDARD",
                    Status = "ACTIVE",
                    SubscriptionStartedAt = now.AddDays(-95),
                    MonthlyAmount = 7000,
                    QueryCountThisMonth = 0,
                    LastReportAt = now.AddDays(-1)
                },
                new()
                {
                    Id = Guid.NewGuid(),
                    FullName = "Suresh Patil",
                    Mobile = "9988776655",
                    BusinessName = "Patil Traders",
                    District = "Tasgaon",
                    Tier = "BASIC",
                    Status = "ACTIVE",
                    SubscriptionStartedAt = now.AddDays(-40),
                    MonthlyAmount = 3000,
                    QueryCountThisMonth = 0,
                    LastReportAt = now.AddDays(-3)
                },
                new()
                {
                    Id = Guid.NewGuid(),
                    FullName = "Mahesh Kale",
                    Mobile = "9001122334",
                    BusinessName = "Kale Mango Exports",
                    District = "Ratnagiri",
                    Tier = "BASIC",
                    Status = "TRIAL",
                    SubscriptionStartedAt = null,
                    MonthlyAmount = 0,
                    QueryCountThisMonth = 0,
                    LastReportAt = now.AddDays(-1)
                },
                new()
                {
                    Id = Guid.NewGuid(),
                    FullName = "Vikram Joshi",
                    Mobile = "9112344321",
                    BusinessName = "Joshi & Sons Cold Storage",
                    District = "Aurangabad",
                    Tier = "STANDARD",
                    Status = "PAUSED",
                    SubscriptionStartedAt = now.AddDays(-200),
                    MonthlyAmount = 7000,
                    QueryCountThisMonth = 0,
                    LastReportAt = now.AddDays(-25)
                }
            };
        }
    }

    /// <summary>
    /// Lists the most recent intelligence reports (weekly / flash / pre-season
    /// / daily). Optional <paramref name="reportType"/> and
    /// <paramref name="commodity"/> are pushed down as PostgREST <c>eq</c>
    /// filters; <c>"ALL"</c> means "no filter". Falls back to placeholder
    /// rows on transport error.
    /// </summary>
    public async Task<List<IntelligenceReportRow>> GetIntelligenceReportsAsync(
        string? reportType = null,
        string? commodity = null,
        int limit = 100)
    {
        var baseUrl = _url.TrimEnd('/');
        var sb = new StringBuilder();
        sb.Append(baseUrl);
        sb.Append("/rest/v1/intelligence_reports");
        sb.Append("?select=*");
        sb.Append("&order=report_date.desc");
        sb.Append("&limit=").Append(limit);

        if (!string.IsNullOrEmpty(reportType) &&
            !string.Equals(reportType, "ALL", StringComparison.OrdinalIgnoreCase))
        {
            sb.Append("&report_type=eq.")
              .Append(Uri.EscapeDataString(reportType));
        }
        if (!string.IsNullOrEmpty(commodity) &&
            !string.Equals(commodity, "ALL", StringComparison.OrdinalIgnoreCase))
        {
            sb.Append("&commodity=eq.")
              .Append(Uri.EscapeDataString(commodity));
        }

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, sb.ToString());
            ApplySupabaseHeaders(request);
            using var response = await HttpClient.SendAsync(request);
            response.EnsureSuccessStatusCode();
            var json = await response.Content.ReadAsStringAsync();
            return JsonSerializer.Deserialize<List<IntelligenceReportRow>>(json, JsonOpts)
                   ?? new List<IntelligenceReportRow>();
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"GetIntelligenceReportsAsync failed, returning placeholder: {ex.Message}");
            var today = DateTime.UtcNow.Date;
            return new List<IntelligenceReportRow>
            {
                new()
                {
                    Id = Guid.NewGuid(),
                    ReportType = "WEEKLY",
                    Commodity = "Dry Grapes",
                    Region = "Tasgaon",
                    ReportDate = today.AddDays(-1),
                    Signal = "BUY",
                    PriceForecastDay1 = 312,
                    PriceForecastDay3 = 318,
                    PriceForecastDay7 = 325,
                    ConfidencePct = 82,
                    RecipientsCount = 18,
                    DeliveredCount = 18,
                    ContentEnglish = "Weekly Market Intelligence — Dry Grapes (Tasgaon). BUY signal. Supply forecast below 3-yr average. Target Rs 325/kg by Day 7."
                },
                new()
                {
                    Id = Guid.NewGuid(),
                    ReportType = "FLASH",
                    Commodity = "Pomegranate",
                    Region = "Solapur",
                    ReportDate = today.AddDays(-3),
                    Signal = "IMMEDIATE_BUY",
                    PriceForecastDay1 = 64,
                    PriceForecastDay3 = 71,
                    PriceForecastDay7 = 74,
                    ConfidencePct = 78,
                    RecipientsCount = 11,
                    DeliveredCount = 11,
                    ContentEnglish = "FLASH ALERT — Pomegranate Solapur. Price drop 9% in one day. Buying window next 24h."
                },
                new()
                {
                    Id = Guid.NewGuid(),
                    ReportType = "DAILY",
                    Commodity = "Mango Alphonso",
                    Region = "Ratnagiri",
                    ReportDate = today,
                    Signal = "HOLD",
                    PriceForecastDay1 = 1450,
                    PriceForecastDay3 = 1480,
                    PriceForecastDay7 = 1495,
                    ConfidencePct = 71,
                    RecipientsCount = 3,
                    DeliveredCount = 3,
                    ContentEnglish = "Daily Update — Alphonso Ratnagiri. Modal Rs 1450/kg. Tomorrow Rs 1450-1480/kg. Signal HOLD."
                }
            };
        }
    }

    /// <summary>
    /// Returns aggregated trader analytics for the dashboard. Preferred path:
    /// hit the internal FastAPI endpoint <c>{Internal:BaseUrl}/traders/analytics</c>
    /// built by Agent 5. On any failure falls back to placeholder analytics
    /// so the KPI cards still render.
    /// </summary>
    public async Task<TraderAnalytics> GetTraderAnalyticsAsync()
    {
        var url = $"{_internalBaseUrl}/traders/analytics";
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            using var response = await HttpClient.SendAsync(request);
            response.EnsureSuccessStatusCode();
            var json = await response.Content.ReadAsStringAsync();
            var analytics = JsonSerializer.Deserialize<TraderAnalytics>(json, JsonOpts);
            if (analytics is not null) return analytics;
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"GetTraderAnalyticsAsync failed, returning placeholder: {ex.Message}");
        }

        return new TraderAnalytics
        {
            TotalTraders = 27,
            ActiveSubscribers = 18,
            TrialUsers = 6,
            PausedUsers = 2,
            CancelledUsers = 1,
            Mrr = 132000,
            ThisMonthRevenue = 132000,
            LastMonthRevenue = 118000,
            ByTier = new TierBreakdown(Basic: 8, Standard: 7, Premium: 3),
            TrialConversionRatePct = 25.0,
            AvgQueryCountPremium = 12.4
        };
    }

    /// <summary>
    /// Manually triggers a report generation run via the internal API.
    /// Routes to <c>/intelligence/generate-weekly</c> for WEEKLY reports,
    /// or <c>/intelligence/generate-flash</c> otherwise. Returns
    /// <c>true</c> only on a 2xx response.
    /// </summary>
    public async Task<bool> TriggerReportGenerationAsync(
        string commodity,
        string reportType = "WEEKLY",
        string? region = null,
        bool send = false)
    {
        var path = string.Equals(reportType, "WEEKLY", StringComparison.OrdinalIgnoreCase)
            ? "/intelligence/generate-weekly"
            : "/intelligence/generate-flash";
        var url = $"{_internalBaseUrl}{path}";

        try
        {
            var body = new
            {
                commodity,
                report_type = reportType,
                region,
                send
            };
            using var response = await HttpClient.PostAsJsonAsync(url, body);
            return response.IsSuccessStatusCode;
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"TriggerReportGenerationAsync failed: {ex.Message}");
            return false;
        }
    }

    /// <summary>
    /// Fetches paid trader_payments rows and groups them client-side by month
    /// to produce an MRR trend series for the last
    /// <paramref name="monthsBack"/> months. Returns a synthetic series on
    /// transport error or when the table is empty so the chart still renders.
    /// </summary>
    public async Task<List<MrrPoint>> GetMrrTrendAsync(int monthsBack = 12)
    {
        var baseUrl = _url.TrimEnd('/');
        var url =
            $"{baseUrl}/rest/v1/trader_payments" +
            "?select=payment_month,amount" +
            "&status=eq.PAID" +
            "&order=payment_month.asc";

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            ApplySupabaseHeaders(request);
            using var response = await HttpClient.SendAsync(request);
            response.EnsureSuccessStatusCode();
            var json = await response.Content.ReadAsStringAsync();
            var rows = JsonSerializer.Deserialize<List<TraderPaymentDto>>(json, JsonOpts)
                       ?? new List<TraderPaymentDto>();

            if (rows.Count > 0)
            {
                var grouped = rows
                    .Where(r => r.PaymentMonth.HasValue)
                    .GroupBy(r => new DateTime(
                        r.PaymentMonth!.Value.Year,
                        r.PaymentMonth!.Value.Month,
                        1))
                    .OrderBy(g => g.Key)
                    .Select(g => new MrrPoint(g.Key, g.Sum(p => p.Amount ?? 0)))
                    .ToList();

                if (grouped.Count == 0) return BuildPlaceholderMrrTrend(monthsBack);

                // Keep only the last `monthsBack` months.
                return grouped.Count <= monthsBack
                    ? grouped
                    : grouped.Skip(grouped.Count - monthsBack).ToList();
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"GetMrrTrendAsync failed, returning placeholder: {ex.Message}");
        }

        return BuildPlaceholderMrrTrend(monthsBack);
    }

    private static List<MrrPoint> BuildPlaceholderMrrTrend(int monthsBack)
    {
        var today = DateTime.UtcNow.Date;
        var firstOfThisMonth = new DateTime(today.Year, today.Month, 1);
        var rng = new Random(2026);
        var points = new List<MrrPoint>();
        double mrr = 24000;
        for (var i = monthsBack - 1; i >= 0; i--)
        {
            var month = firstOfThisMonth.AddMonths(-i);
            mrr += 8500 + rng.Next(0, 4500);
            points.Add(new MrrPoint(month, Math.Round(mrr)));
        }
        return points;
    }

    private sealed class TraderPaymentDto
    {
        [JsonPropertyName("payment_month")] public DateTime? PaymentMonth { get; set; }
        [JsonPropertyName("amount")] public double? Amount { get; set; }
    }

    // ─── Mango Crop Expansion (May 2026 — Mango Agent 8) ──────────────

    /// <summary>
    /// Returns rows from <c>mango_phenology_log</c>, optionally filtered to a
    /// single <c>plot_id</c>. Falls back to deterministic placeholder data so
    /// the Mango Intelligence sub-section and the Belt forecast still render
    /// without a live DB.
    /// </summary>
    /// <remarks>
    /// TODO: when the <c>mango_phenology_log</c> table is provisioned by
    /// Mango Agent 1, drop the placeholder block and rely entirely on the
    /// HTTP response.
    /// </remarks>
    public async Task<List<MangoPhenologyRow>> GetMangoPhenologyAsync(Guid? plotId = null)
    {
        var baseUrl = _url.TrimEnd('/');
        var sb = new StringBuilder();
        sb.Append(baseUrl);
        sb.Append("/rest/v1/mango_phenology_log");
        sb.Append("?select=plot_id,season_label,bearing_year,");
        sb.Append("flowering_start_date,flowering_peak_date,flowering_intensity_pct,");
        sb.Append("frost_events_count,heat_stress_events_count,");
        sb.Append("fruit_set_date,fruit_set_pct,predicted_yield_kg_per_tree");
        sb.Append("&order=season_label.desc");

        if (plotId.HasValue && plotId.Value != Guid.Empty)
        {
            sb.Append("&plot_id=eq.").Append(plotId.Value);
        }

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, sb.ToString());
            ApplySupabaseHeaders(request);
            using var response = await HttpClient.SendAsync(request);
            response.EnsureSuccessStatusCode();
            var json = await response.Content.ReadAsStringAsync();
            var dtos = JsonSerializer.Deserialize<List<MangoPhenologyRowDto>>(json, JsonOpts)
                       ?? new List<MangoPhenologyRowDto>();

            if (dtos.Count > 0)
            {
                return dtos.Select(d => new MangoPhenologyRow
                {
                    PlotId = d.PlotId ?? Guid.Empty,
                    SeasonLabel = d.SeasonLabel ?? "",
                    BearingYear = d.BearingYear ?? "UNKNOWN",
                    FloweringStartDate = d.FloweringStartDate,
                    FloweringPeakDate = d.FloweringPeakDate,
                    FloweringIntensityPct = d.FloweringIntensityPct,
                    FrostEventsCount = d.FrostEventsCount ?? 0,
                    HeatStressEventsCount = d.HeatStressEventsCount ?? 0,
                    FruitSetDate = d.FruitSetDate,
                    FruitSetPct = d.FruitSetPct,
                    PredictedYieldKgPerTree = d.PredictedYieldKgPerTree
                }).ToList();
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"GetMangoPhenologyAsync failed, returning placeholder: {ex.Message}");
        }

        // TODO: replace placeholders once mango_phenology_log is live.
        var samplePlot = plotId ?? Guid.NewGuid();
        return new List<MangoPhenologyRow>
        {
            new()
            {
                PlotId = samplePlot,
                SeasonLabel = "2025-26",
                BearingYear = "ON",
                FloweringStartDate = new DateTime(2025, 12, 18),
                FloweringPeakDate = new DateTime(2026, 1, 8),
                FloweringIntensityPct = 78,
                FrostEventsCount = 0,
                HeatStressEventsCount = 2,
                FruitSetDate = new DateTime(2026, 2, 4),
                FruitSetPct = 64,
                PredictedYieldKgPerTree = 92
            },
            new()
            {
                PlotId = samplePlot,
                SeasonLabel = "2024-25",
                BearingYear = "OFF",
                FloweringStartDate = new DateTime(2024, 12, 28),
                FloweringPeakDate = new DateTime(2025, 1, 18),
                FloweringIntensityPct = 41,
                FrostEventsCount = 1,
                HeatStressEventsCount = 3,
                FruitSetDate = new DateTime(2025, 2, 12),
                FruitSetPct = 33,
                PredictedYieldKgPerTree = 48
            }
        };
    }

    /// <summary>
    /// Returns rows from <c>mango_belt_data</c> for a region (Konkan / Nashik
    /// / Vidarbha / Other), optionally narrowed to a variety. Falls back to
    /// deterministic placeholder data on transport error.
    /// </summary>
    public async Task<List<MangoBeltRow>> GetMangoBeltDataAsync(
        string region,
        string? variety = null,
        int limit = 30)
    {
        var baseUrl = _url.TrimEnd('/');
        var sb = new StringBuilder();
        sb.Append(baseUrl);
        sb.Append("/rest/v1/mango_belt_data");
        sb.Append("?select=region,variety,fetch_date,total_fields_detected,");
        sb.Append("total_area_acres,bearing_year,fields_harvesting,");
        sb.Append("estimated_volume_mt,health_pct_good,flowering_pct,fruit_set_pct");
        sb.Append("&region=eq.").Append(Uri.EscapeDataString(region));
        if (!string.IsNullOrEmpty(variety))
        {
            sb.Append("&variety=eq.").Append(Uri.EscapeDataString(variety));
        }
        sb.Append("&order=fetch_date.desc");
        sb.Append("&limit=").Append(limit);

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, sb.ToString());
            ApplySupabaseHeaders(request);
            using var response = await HttpClient.SendAsync(request);
            response.EnsureSuccessStatusCode();
            var json = await response.Content.ReadAsStringAsync();
            var dtos = JsonSerializer.Deserialize<List<MangoBeltRowDto>>(json, JsonOpts)
                       ?? new List<MangoBeltRowDto>();

            if (dtos.Count > 0)
            {
                return dtos.Select(d => new MangoBeltRow
                {
                    Region = d.Region ?? "",
                    Variety = d.Variety ?? "",
                    FetchDate = d.FetchDate ?? DateTime.UtcNow,
                    TotalFieldsDetected = d.TotalFieldsDetected ?? 0,
                    TotalAreaAcres = d.TotalAreaAcres ?? 0,
                    BearingYear = d.BearingYear,
                    FieldsHarvesting = d.FieldsHarvesting ?? 0,
                    EstimatedVolumeMt = d.EstimatedVolumeMt ?? 0,
                    HealthPctGood = d.HealthPctGood,
                    FloweringPct = d.FloweringPct,
                    FruitSetPct = d.FruitSetPct
                }).ToList();
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"GetMangoBeltDataAsync failed, returning placeholder: {ex.Message}");
        }

        // TODO: replace placeholders once mango_belt_data is provisioned.
        return BuildPlaceholderMangoBelt(region, variety, limit);
    }

    private static List<MangoBeltRow> BuildPlaceholderMangoBelt(string region, string? variety, int limit)
    {
        var rng = new Random(region.GetHashCode());
        var today = DateTime.UtcNow.Date;
        var resolvedVariety = variety ?? region switch
        {
            "Konkan" => "Alphonso",
            "Nashik" => "Kesar",
            "Vidarbha" => "Dasheri",
            _ => "Alphonso"
        };
        var bearingCycle = new[] { "ON", "OFF" };
        var rows = new List<MangoBeltRow>();
        var weeks = Math.Min(limit, 8);
        for (var i = 0; i < weeks; i++)
        {
            rows.Add(new MangoBeltRow
            {
                Region = region,
                Variety = resolvedVariety,
                FetchDate = today.AddDays(-7 * i),
                TotalFieldsDetected = 1200 + rng.Next(0, 800),
                TotalAreaAcres = 4200 + rng.NextDouble() * 1800,
                BearingYear = bearingCycle[i % 2],
                FieldsHarvesting = 240 + rng.Next(0, 220),
                EstimatedVolumeMt = 320 + rng.NextDouble() * 280,
                HealthPctGood = 58 + rng.NextDouble() * 16,
                FloweringPct = 40 + rng.NextDouble() * 35,
                FruitSetPct = 30 + rng.NextDouble() * 30
            });
        }
        return rows;
    }

    private sealed class MangoPhenologyRowDto
    {
        [JsonPropertyName("plot_id")] public Guid? PlotId { get; set; }
        [JsonPropertyName("season_label")] public string? SeasonLabel { get; set; }
        [JsonPropertyName("bearing_year")] public string? BearingYear { get; set; }
        [JsonPropertyName("flowering_start_date")] public DateTime? FloweringStartDate { get; set; }
        [JsonPropertyName("flowering_peak_date")] public DateTime? FloweringPeakDate { get; set; }
        [JsonPropertyName("flowering_intensity_pct")] public double? FloweringIntensityPct { get; set; }
        [JsonPropertyName("frost_events_count")] public int? FrostEventsCount { get; set; }
        [JsonPropertyName("heat_stress_events_count")] public int? HeatStressEventsCount { get; set; }
        [JsonPropertyName("fruit_set_date")] public DateTime? FruitSetDate { get; set; }
        [JsonPropertyName("fruit_set_pct")] public double? FruitSetPct { get; set; }
        [JsonPropertyName("predicted_yield_kg_per_tree")] public double? PredictedYieldKgPerTree { get; set; }
    }

    private sealed class MangoBeltRowDto
    {
        [JsonPropertyName("region")] public string? Region { get; set; }
        [JsonPropertyName("variety")] public string? Variety { get; set; }
        [JsonPropertyName("fetch_date")] public DateTime? FetchDate { get; set; }
        [JsonPropertyName("total_fields_detected")] public int? TotalFieldsDetected { get; set; }
        [JsonPropertyName("total_area_acres")] public double? TotalAreaAcres { get; set; }
        [JsonPropertyName("bearing_year")] public string? BearingYear { get; set; }
        [JsonPropertyName("fields_harvesting")] public int? FieldsHarvesting { get; set; }
        [JsonPropertyName("estimated_volume_mt")] public double? EstimatedVolumeMt { get; set; }
        [JsonPropertyName("health_pct_good")] public double? HealthPctGood { get; set; }
        [JsonPropertyName("flowering_pct")] public double? FloweringPct { get; set; }
        [JsonPropertyName("fruit_set_pct")] public double? FruitSetPct { get; set; }
    }
}
