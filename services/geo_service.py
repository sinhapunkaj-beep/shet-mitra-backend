import math

MANDI_COORDS = {
    "Pune": (18.5204, 73.8567),
    "Nashik": (19.9975, 73.7898),
    "Nagpur": (21.1458, 79.0882)
}


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    return R * c


def sort_by_distance(data, user_lat=18.5204, user_lon=73.8567):
    result = []

    for item in data:
        mandi = item.get("mandi")

        if mandi in MANDI_COORDS:
            lat, lon = MANDI_COORDS[mandi]
            dist = haversine(user_lat, user_lon, lat, lon)
        else:
            dist = 999

        item["distance_km"] = round(dist, 2)
        result.append(item)

    return sorted(result, key=lambda x: x["distance_km"])