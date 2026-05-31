"""End-to-end verification: AMED mock -> pipeline -> harvest window -> mismatch -> price prediction."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geo.amed_client import AMEDClient
from pipelines.ndvi_pipeline import AmedSentinelPipeline
from utils.price_prediction import PricePredictor


async def main():
    print("=" * 70)
    print("ShetMitra AMED end-to-end verification")
    print("=" * 70)

    client = AMEDClient()
    pipeline = AmedSentinelPipeline(amed_client=client)

    plot = {
        "id": "plot-e2e-001",
        "current_crop": "Grapes",
        "area_acres": 3.2,
        "boundary_polygon": [
            [17.0374, 74.5958],
            [17.0380, 74.5965],
            [17.0370, 74.5970],
            [17.0364, 74.5963],
        ],
        "region": "Tasgaon_Sangli_belt",
    }

    print("\n[1] Running pipeline for plot-e2e-001 (Grapes, 3.2 acres)...")
    result = await pipeline.run_full_pipeline(farmer_id="farmer-e2e-001", plot=plot)
    print(f"    harvest_source     : {result.get('harvest_source')}")
    print(f"    harvest_window     : {result['harvest_window']['start']} -> {result['harvest_window']['end']}")
    print(f"    harvest_confidence : {result.get('harvest_confidence'):.3f}")
    print(f"    combined_health    : {result['combined_health']['category']} (score={result['combined_health']['score']:.3f})")
    print(f"    mismatches         : {len(result['mismatches'])}")
    print(f"    belt this-week MT  : {result['belt']['harvest_forecast'][0]['estimated_volume_mt']}")
    print(f"    errors             : {result['errors'] or 'none'}")

    print("\n[2] Mismatch detection (Grapes registered but AMED returns Pomegranate)...")
    pom_plot = dict(plot, id="plot-e2e-002", current_crop="Pomegranate", area_acres=5.5)
    pom_result = await pipeline.run_full_pipeline(farmer_id="farmer-e2e-002", plot=pom_plot)
    mismatch_types = sorted({m.get("type") for m in pom_result["mismatches"]})
    print(f"    detected mismatch types: {mismatch_types or 'none'}")

    print("\n[3] Price prediction (Dry_Grapes, with belt volume forecast)...")
    predictor = PricePredictor()
    current_features = {
        "price_lag_1": 240.0,
        "price_lag_7": 235.0,
        "price_lag_14": 230.0,
        "arrivals_lag_1": 40.0,
        "arrivals_7day_avg": 38.0,
        "season_week": 3,
        "month": 4,
        "year": 2026,
        "price_yoy": 1.4,
        "arrivals_yoy": 1.1,
        "amed_belt_volume_mt": 680.0,
        "amed_fields_harvesting": 467,
        "amed_health_pct_good": 0.63,
        "amed_season_timing_dev": 0.0,
    }
    pred = predictor.predict_price(
        commodity="Dry_Grapes",
        current_features=current_features,
        belt_volume_forecast=[680.0, 450.0],
    )
    print(f"    model_version    : {pred['model_version']}")
    print(f"    predictions (3d) : {[round(p, 2) for p in pred['predictions']]}")
    print(f"    confidence       : {pred['confidence']:.3f}")
    print(f"    supply_pressure  : {pred['supply_pressure']}")

    print("\n[4] Auto-forecast fallback (no belt_volume_forecast)...")
    pred2 = predictor.predict_price(commodity="Dry_Grapes", current_features=current_features)
    print(f"    predictions (3d) : {[round(p, 2) for p in pred2['predictions']]}")
    print(f"    supply_pressure  : {pred2['supply_pressure']}")

    print("\nEnd-to-end verification: SUCCESS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
