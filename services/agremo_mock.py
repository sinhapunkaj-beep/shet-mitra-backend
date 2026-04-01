import random
from config import FIELD_ID, CROP

def get_mock_data():
    return {
        "field_id": FIELD_ID,
        "date": "2026-04-01",
        "crop": CROP,
        "metrics": {
            "ndvi_avg": round(random.uniform(0.5, 0.8), 2),
            "plant_count": random.randint(900, 1300),
            "stress_zones_percent": random.randint(5, 30),
            "uniformity": round(random.uniform(0.6, 0.9), 2)
        },
        "source": "mock"
    }