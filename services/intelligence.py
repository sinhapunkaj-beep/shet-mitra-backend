# services/intelligence.py

from datetime import datetime, timedelta

# -----------------------------
# CONFIG (can be tuned later)
# -----------------------------
COST_PER_KM = 2.5   # ₹ per km
SHIFT_THRESHOLD = 0.10  # 10%
STALE_HOURS = 48


# -----------------------------
# DATA CLEANING
# -----------------------------
def clean_price_data(records):
    cleaned = []

    for r in records:
        price = r.get("price", 0)

        # Ignore bad data
        if price is None or price == 0:
            continue

        cleaned.append(r)

    return cleaned


# -----------------------------
# TREND (3-day SMA)
# -----------------------------
def calculate_trend(records):
    if len(records) < 3:
        return "stable"

    prices = [r["price"] for r in records[-3:]]

    sma_old = sum(prices[:2]) / 2
    sma_new = sum(prices[1:]) / 2

    if sma_new > sma_old:
        return "rising"
    elif sma_new < sma_old:
        return "falling"
    else:
        return "stable"


# -----------------------------
# NET PROFIT CALCULATION
# -----------------------------
def calculate_net_price(price, distance):
    transport_cost = distance * COST_PER_KM
    return price - transport_cost


# -----------------------------
# DATA FRESHNESS
# -----------------------------
def check_data_freshness(records):
    if not records:
        return "low"

    latest_date = records[-1].get("date")

    if not latest_date:
        return "low"

    latest_date = datetime.strptime(latest_date, "%Y-%m-%d")
    diff = datetime.now() - latest_date

    if diff > timedelta(hours=STALE_HOURS):
        return "low"
    elif diff > timedelta(hours=24):
        return "medium"
    else:
        return "high"


# -----------------------------
# MAIN DECISION ENGINE
# -----------------------------
def generate_recommendation(mandi_data, farmer_mandi):
    """
    mandi_data = [
        {
            "mandi": "Kurnool",
            "price": 1300,
            "distance": 80,
            "date": "2026-04-03"
        },
        ...
    ]
    """

    mandi_data = clean_price_data(mandi_data)

    if not mandi_data:
        return {
            "verdict": "NO DATA",
            "reason": "No valid price data available",
            "confidence": "low"
        }

    # Get local mandi
    local = next((m for m in mandi_data if m["mandi"] == farmer_mandi), None)

    if not local:
        return {
            "verdict": "ERROR",
            "reason": "Local mandi not found",
            "confidence": "low"
        }

    # Calculate trend
    trend = calculate_trend(mandi_data)

    # Calculate best mandi
    best = None
    best_net = -99999

    for m in mandi_data:
        net = calculate_net_price(m["price"], m["distance"])

        if net > best_net:
            best_net = net
            best = m

    local_net = calculate_net_price(local["price"], local["distance"])

    gain = (best_net - local_net) / max(local_net, 1)

    # -----------------------------
    # DECISION LOGIC
    # -----------------------------
    if trend == "falling":
        if gain > SHIFT_THRESHOLD:
            verdict = "SHIFT"
            reason = f"Better price in {best['mandi']} even after transport"
        else:
            verdict = "SELL NOW"
            reason = "Prices are falling"

    elif trend == "rising":
        if best["mandi"] != farmer_mandi and gain > SHIFT_THRESHOLD:
            verdict = "HOLD"
            reason = "Prices are rising, wait for peak"
        else:
            verdict = "HOLD"
            reason = "Prices are increasing"

    else:  # stable
        if gain > SHIFT_THRESHOLD:
            verdict = "SHIFT"
            reason = f"Higher net profit in {best['mandi']}"
        else:
            verdict = "SELL NOW"
            reason = "Stable prices, sell now"

    # -----------------------------
    # CONFIDENCE
    # -----------------------------
    confidence = check_data_freshness(mandi_data)

    return {
        "verdict": verdict,
        "best_mandi": best["mandi"],
        "expected_price": best["price"],
        "trend": trend,
        "reason": reason,
        "confidence": confidence
    }

import math

def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Haversine formula to calculate distance in KM
    """
    R = 6371  # Earth radius in km

    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c