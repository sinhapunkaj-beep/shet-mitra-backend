from services.intelligence import generate_recommendation

data = [
    {"mandi": "Anantapur", "price": 1100, "distance": 0, "date": "2026-04-01"},
    {"mandi": "Kurnool", "price": 1350, "distance": 80, "date": "2026-04-02"},
    {"mandi": "Guntur", "price": 1250, "distance": 120, "date": "2026-04-03"},
]

result = generate_recommendation(data, "Anantapur")

print(result)