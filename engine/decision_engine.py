# engine/decision_engine.py

def farmer_decision_engine(data):
    current_price = data.get("current_price", 0)
    avg_7 = data.get("avg_7_days", 0)
    trend = data.get("trend", "STABLE")
    alerts = data.get("alerts", [])
    best_mandi = data.get("best_mandi", "Unknown")
    best_price = data.get("best_mandi_price", current_price)

    PRICE_DIFF_THRESHOLD = 100  # ₹ difference to consider shifting

    price_strength = "HIGH" if current_price > avg_7 else "LOW"
    price_diff = best_price - current_price

    # Decision Logic
    if price_diff > PRICE_DIFF_THRESHOLD:
        decision = "SHIFT"

    elif trend == "UP" or "PRICE_RISING" in alerts:
        decision = "HOLD"

    elif price_strength == "HIGH" and trend == "DOWN":
        decision = "SELL"

    else:
        decision = "HOLD"

    # Confidence Score
    if trend == "UP":
        confidence = 85
    elif trend == "DOWN":
        confidence = 75
    else:
        confidence = 60

    # Reasoning (NO PROFIT CLAIMS)
    if decision == "SHIFT":
        reason = f"Better selling price available in {best_mandi} (₹{best_price})"

    elif decision == "HOLD":
        reason = "Prices are rising or stable. You may get a better selling price"

    elif decision == "SELL":
        reason = "Prices are likely to fall. Selling now may secure current price"

    return {
        "decision": decision,
        "reason": reason,
        "best_mandi": best_mandi,
        "best_price": best_price,
        "price_diff": price_diff,
        "confidence": confidence
    }