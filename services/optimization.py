import psycopg2
from config import DB_CONFIG
from utils.distance import calculate_distance


def get_best_mandi(farmer_id, crop):

    try:
        conn = psycopg2.connect(
            host=DB_CONFIG["host"],
            database=DB_CONFIG["database"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            port=DB_CONFIG["port"],
            sslmode="require"
        )

        cur = conn.cursor()

        # ✅ Farmer location (NO mandi column)
        cur.execute("""
            SELECT latitude, longitude
            FROM farmers
            WHERE farmer_id = %s
        """, (farmer_id,))
        
        farmer = cur.fetchone()

        if not farmer:
            return {"error": "Farmer not found"}

        farmer_lat, farmer_lon = farmer
        current_market = "Your Location"

        # ✅ Market data
        cur.execute("""
            SELECT mandi, modal_price, latitude, longitude
            FROM market_prices
            WHERE LOWER(crop) = LOWER(%s)
            AND latitude IS NOT NULL
            AND modal_price IS NOT NULL
        """, (crop,))

        rows = cur.fetchall()

        if not rows:
            return {
                "current_market": current_market,
                "recommendations": []
            }

        results = []

        # ✅ Scoring logic
        for mandi, price, lat, lon in rows:

            distance = calculate_distance(
                farmer_lat, farmer_lon, lat, lon
            )

            score = price - (distance * 5)

            results.append({
                "mandi": mandi,
                "price": round(price, 2),
                "distance_km": round(distance, 2),
                "score": round(score, 2)
            })

        best = sorted(results, key=lambda x: x["score"], reverse=True)

        cur.close()
        conn.close()

        return {
            "current_market": current_market,
            "recommendations": best[:3]
        }

    except Exception as e:
        print("ERROR:", e)
        return {"error": str(e)}