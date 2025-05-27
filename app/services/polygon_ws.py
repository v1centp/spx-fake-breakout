# services/polygon_ws.py

from polygon import WebSocketClient
from polygon.websocket.models import Feed, Market, EquityAgg
from typing import List
from threading import Thread
from app.services.firebase import get_firestore
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

def handle_msg(msgs: List[EquityAgg]):
    db = get_firestore()

    for m in msgs:
        print(f"Received: {type(m)} → {m}")

        try:
            candle = {
                "ev": m.event_type,
                "sym": m.symbol,
                "op": m.official_open_price,
                "o": m.open,
                "c": m.close,
                "h": m.high,
                "l": m.low,
                "s": m.start_timestamp,
                "e": m.end_timestamp,
                "utc_time": datetime.fromtimestamp(m.end_timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            }

            doc_id = f"{m.symbol}_{m.end_timestamp}"
            db.collection("ohlc_1m").document(doc_id).set(candle)
            print(f"✅ Stored {m.symbol} candle at {candle['utc_time']}")
        except Exception as e:
            print(f"⚠️ Error processing message: {e}")

def start_polygon_ws():
    client = WebSocketClient(
        api_key=POLYGON_API_KEY,
        feed=Feed.RealTime,
        market=Market.Indices
    )
    client.subscribe("AM.I:SPX")  # Subscribe to SPX index
    thread = Thread(target=client.run, args=(handle_msg,), daemon=True)
    thread.start()
