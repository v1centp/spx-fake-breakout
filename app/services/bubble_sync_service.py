import os
import requests
from datetime import datetime
from app.services.oanda_service import get_open_trades

BUBBLE_API_URL = os.getenv("BUBBLE_API_URL")
BUBBLE_API_KEY = os.getenv("BUBBLE_API_KEY")

headers = {
    "Authorization": f"Bearer {BUBBLE_API_KEY}",
    "Content-Type": "application/json"
}

def sync_trades_to_bubble():
    trades = get_open_trades()
    print(f"📦 Open trades trouvés : {len(trades)}")

    for trade in trades:
        trade_id = trade["id"]
        instrument = trade["instrument"]
        units = float(trade["currentUnits"])
        entry_price = float(trade["price"])
        unrealized_pl = float(trade["unrealizedPL"])

        payload = {
            "trade_id": trade_id,
            "instrument": instrument,
            "units": units,
            "entry_price": entry_price,
            "unrealized_pl": unrealized_pl,
            "timestamp": datetime.utcnow().isoformat()
        }

        # Vérifier si le trade existe déjà dans Bubble
        check_url = f'{BUBBLE_API_URL}?constraints=[{{"key":"trade_id","constraint_type":"equals","value":"{trade_id}"}}]'
        res = requests.get(check_url, headers=headers)
        res.raise_for_status()
        existing = res.json().get("response", {}).get("results", [])

        if existing:
            obj_id = existing[0]["_id"]
            patch_url = f"{BUBBLE_API_URL}/{obj_id}"
            patch_res = requests.patch(patch_url, json=payload, headers=headers)
            print(f"🔁 PATCH {instrument} | trade {trade_id} ➜ {patch_res.status_code}")
        else:
            post_res = requests.post(BUBBLE_API_URL, json=payload, headers=headers)
            print(f"🆕 POST {instrument} | trade {trade_id} ➜ {post_res.status_code}")
            