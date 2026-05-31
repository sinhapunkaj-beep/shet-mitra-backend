def generate_alerts(data):
    alerts = []

    for d in data:
        price_min = float(d.get("price_min", 0))
        price_max = float(d.get("price_max", 0))
        price_modal = float(d.get("price_modal", 0))

        diff = price_max - price_min

        # High volatility
        if price_min > 0 and diff > price_min * 0.3:
            alerts.append(
                f"🚀 High volatility in {d.get('mandi')} for {d.get('commodity')}"
            )

        # Peak price
        if price_modal == price_max and price_max > 0:
            alerts.append(
                f"🔥 Peak price in {d.get('mandi')} for {d.get('commodity')}"
            )

    return alerts