import os
import requests
from datetime import datetime
from app.services.oanda_service import get_open_positions

BUBBLE_API_URL = os.getenv("BUBBLE_API_URL")  # should end in /position
BUBBLE_API_KEY = os.getenv("BUBBLE_API_KEY")

headers = {
    "Authorization": f"Bearer {BUBBLE_API_KEY}",
    "Content-Type": "application/json"
}


def sync_positions_to_bubble():
    positions = get_open_positions()
    print(f"📦 Positions à traiter : {len(positions)}")

    for pos in positions:
        instrument = pos["instrument"]
        trade_ids = pos["long"].get("tradeIDs", []) + pos["short"].get("tradeIDs", [])

        for trade_id in trade_ids:
            # Requête GET pour vérifier si trade_id existe déjà dans Bubble
            check_url = f'{BUBBLE_API_URL}?constraints=[{{"key":"trade_id","constraint_type":"equals","value":"{trade_id}"}}]'
            res = requests.get(check_url, headers=headers)
            res.raise_for_status()
            existing = res.json().get("response", {}).get("results", [])

            payload = {
                "trade_id": trade_id,
                "instrument": instrument,
                "long_avg_price": float(pos["long"]["averagePrice"]),
                "long_units": float(pos["long"]["units"]),
                "margin_used": float(pos["marginUsed"]),
                "unrealized_pl": float(pos["unrealizedPL"]),
                "total_pl": float(pos["pl"]),
                "timestamp": datetime.now().isoformat()
            }

            if existing:
                # PATCH → mettre à jour
                obj_id = existing[0]["_id"]
                update_url = f"{BUBBLE_API_URL}/{obj_id}"
                patch_res = requests.patch(update_url, json=payload, headers=headers)
                print(f"🔁 PATCH {trade_id} ➜ {patch_res.status_code}")
            else:
                # POST → créer
                post_res = requests.post(BUBBLE_API_URL, json=payload, headers=headers)
                print(f"🆕 POST {trade_id} ➜ {post_res.status_code}")
