from fastapi import APIRouter, Query
from services.ceda_service import get_mandi_prices
from services.location_service import get_district_from_pincode
from services.geo_service import sort_by_distance

# 🔥 THIS LINE MUST EXIST
router = APIRouter()


def extract_varieties(data):
    result = []

    for item in data:
        commodity = (item.get("commodity") or "").strip()
        variety = (item.get("variety") or "").strip()

        # Clean logic
        if not variety or variety.lower() == "other":
            name = commodity
        elif variety.lower() in commodity.lower():
            name = commodity
        elif commodity.lower() in variety.lower():
            name = variety
        else:
            name = f"{commodity} - {variety}"

        result.append({
            "variety": name,
            "grade": item.get("grade"),
            "price": item.get("modal_price")
        })

    return result[:10]
@router.get("/mandi-prices")
def mandi_prices(
    commodity: str = None,
    pincode: str = Query(None)
):
    district = None
    state = None

    # Location detection
    if pincode:
        loc = get_district_from_pincode(pincode)
        if loc:
            district = loc["district"]
            state = loc["state"]

    mandi_data = get_mandi_prices(
        state=state,
        commodity=commodity,
        district=district
    )

    if isinstance(mandi_data, dict) and "error" in mandi_data:
        return {"status": "error", "message": mandi_data["error"]}

    sorted_data = sort_by_distance(mandi_data)

    return {
        "status": "success",
        "filters": {
            "state": state,
            "district": district,
            "commodity": commodity
        },
        "count": len(sorted_data),
        "top_mandis": sorted_data[:5],
        "variety_prices": extract_varieties(sorted_data)
    }

from fastapi.responses import StreamingResponse
import io
import csv


@router.get("/mandi-prices-csv")
def mandi_prices_csv(
    commodity: str = None,
    pincode: str = Query(None)
):
    district = None
    state = None

    # Location detection (optional)
    if pincode:
        loc = get_district_from_pincode(pincode)
        if loc:
            district = loc["district"]
            state = loc["state"]

    data = get_mandi_prices(
        state=state,
        commodity=commodity,
        district=district
    )

    if isinstance(data, dict) and "error" in data:
        return {"status": "error", "message": data["error"]}

    # 🔥 CREATE CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "state",
        "district",
        "mandi",
        "commodity",
        "variety",
        "grade",
        "arrival_date",
        "modal_price"
    ])

    # Rows
    for item in data:
        writer.writerow([
            item.get("state"),
            item.get("district"),
            item.get("mandi"),
            item.get("commodity"),
            item.get("variety"),
            item.get("grade"),
            item.get("arrival_date"),
            item.get("modal_price")
        ])

    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=mandi_data.csv"}
    )