# services/polygon_ws.py

import os
import time
from threading import Thread
from typing import List
from datetime import datetime, timezone
import pytz
from dotenv import load_dotenv
from polygon import WebSocketClient
from polygon.websocket.models import Feed, Market, EquityAgg

from app.services.firebase import get_firestore
from app.services.strategy_logic import process_new_minute_bar
from app.services.range_manager import calculate_and_store_opening_range
from app.services.log_service import log_to_firestore

load_dotenv()
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

def handle_msg(msgs: List[EquityAgg]):
    db = get_firestore()

    for m in msgs:
        try:
            dt_utc = datetime.fromtimestamp(m.end_timestamp / 1000, tz=timezone.utc)
            dt_ny = dt_utc.astimezone(pytz.timezone("America/New_York"))
            is_opening_range = dt_ny.time() >= datetime.strptime("09:30", "%H:%M").time() and \
                               dt_ny.time() <= datetime.strptime("09:45", "%H:%M").time()

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
                "utc_time": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "day": dt_utc.strftime("%Y-%m-%d"),
                "in_opening_range": is_opening_range
            }

            doc_id = f"{m.symbol}_{m.end_timestamp}"
            db.collection("ohlc_1m").document(doc_id).set(candle)
            print(f"✅ Stored {m.symbol} candle at {candle['utc_time']} (in range: {is_opening_range})")

            if not is_opening_range:
                process_new_minute_bar(candle)

            if dt_ny.time().strftime("%H:%M") == "09:45":
                day_str = dt_ny.strftime("%Y-%m-%d")
                print(f"🕒 09:45 NY → Calcul du range pour {day_str}")
                log_to_firestore(f"🕒 09:45 NY → Calcul du range pour {day_str}")
                calculate_and_store_opening_range(day_str)

        except Exception as e:
            print(f"⚠️ Error processing message: {e}")

def start_polygon_ws():
    def connect_with_retry():
        while True:
            try:
                client = WebSocketClient(
                    api_key=POLYGON_API_KEY,
                    feed=Feed.RealTime,
                    market=Market.Indices
                )
                client.subscribe("AM.I:SPX")
                print("🔌 Connexion WebSocket SPX ouverte")
                # log_to_firestore("🔌 Connexion WebSocket SPX établie avec succès")
                client.run(handle_msg)
                print("⚠️ WebSocket fermée, tentative de reconnexion dans 5s...")
            except Exception as e:
                print(f"❌ Erreur WebSocket : {e}")
                log_to_firestore(f"❌ WebSocket crashed: {e}")
            time.sleep(5)

    thread = Thread(target=connect_with_retry, daemon=True)
    thread.start()
