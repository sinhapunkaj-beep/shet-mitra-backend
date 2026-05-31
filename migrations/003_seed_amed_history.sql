-- ShetMitra AMED Integration Migration 003
-- Date: 2026-05-31
-- Description: Seed amed_history with three seasons of Tasgaon/Sangli belt
-- grape data as specified in SDD Section 7 (Agent 2 — Mock History Data).
-- Uses ON CONFLICT (region, season_label, crop_type) DO NOTHING so re-runs
-- are idempotent.

INSERT INTO amed_history (
    region,
    season_label,
    season_year_start,
    crop_type,
    total_area_acres,
    harvest_start_date,
    harvest_peak_date,
    harvest_end_date,
    estimated_total_volume_mt,
    avg_price_modal_kg
) VALUES
    (
        'Tasgaon_Sangli_belt',
        '2022-23',
        2022,
        'Grapes',
        7890,
        DATE '2023-04-01',
        DATE '2023-04-21',
        DATE '2023-05-15',
        11420,
        118.50
    ),
    (
        'Tasgaon_Sangli_belt',
        '2023-24',
        2023,
        'Grapes',
        8012,
        DATE '2024-04-05',
        DATE '2024-04-19',
        DATE '2024-05-10',
        11680,
        134.20
    ),
    (
        'Tasgaon_Sangli_belt',
        '2024-25',
        2024,
        'Grapes',
        8180,
        DATE '2025-03-28',
        DATE '2025-04-14',
        DATE '2025-05-08',
        11890,
        298.40
    )
ON CONFLICT (region, season_label, crop_type) DO NOTHING;
