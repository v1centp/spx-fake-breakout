# app/services/bubble_sync_service.py

import os
import requests
from datetime import datetime
from app.services.oanda_service import get_open_positions
from dotenv import load_dotenv
load_dotenv()

BUBBLE_API_URL = os.getenv("BUBBLE_API_URL")
BUBBLE_API_KEY = os.getenv("BUBBLE_API_KEY")

headers_bubble = {
    "Authorization": f"Bearer {BUBBLE_API_KEY}",
    "Content-Type": "application/json"
}

def sync_positions_to_bubble():
    positions = get_open_positions()

    for pos in positions:
      payload = {
         "instrument": pos["instrument"],
         "long_units": float(pos["long"]["units"]),
         "long_avg_price": float(pos["long"]["averagePrice"]),
         "unrealized_pl": float(pos["unrealizedPL"]),
         "margin_used": float(pos["marginUsed"]),
         "total_pl": float(pos["pl"]),
         "timestamp": datetime.utcnow().isoformat()
      }

      response = requests.post(BUBBLE_API_URL, json=payload, headers=headers_bubble)

      if response.status_code != 200:
         print(f"❌ Error posting {payload['instrument']}: {response.status_code}")
         print(response.text)
      else:
         print(f"✅ Synced {payload['instrument']}")
      