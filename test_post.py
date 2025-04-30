import requests
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

BUBBLE_API_URL = os.getenv("BUBBLE_API_URL")
BUBBLE_API_KEY = os.getenv("BUBBLE_API_KEY")

url = "https://algo-74702.bubbleapps.io/version-test/api/1.1/obj/position"
headers = {
    "Authorization": f"Bearer {BUBBLE_API_KEY}",
    "Content-Type": "application/json"
}
data = {
    "instrument": "SPX500_USD",
    "long_avg_price": 5452.9,
    "long_units": 1.2,
    "margin_used": 1,
    "timestamp": datetime.now().isoformat(),
    "total_pl": 867.4695,
    "unrealized_pl": 0.6882,
}

response = requests.post(url, headers=headers, json=data)
print(response.status_code)
print(response.text)
