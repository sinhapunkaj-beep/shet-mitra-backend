import requests
from config import LAT, LON

def get_weather():
    url = f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&daily=temperature_2m_max,precipitation_sum"
    return requests.get(url).json()