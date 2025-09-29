# app/services/polygon_ws.py
from polygon import WebSocketClient
from polygon.websocket.models import Feed, Market, EquityAgg
from typing import List
from threading import Thread
from app.services.firebase import get_firestore
from datetime import datetime, timezone
from dotenv import load_dotenv
from app.services.range_manager import calculate_and_store_opening_range
from app.services.log_service import log_to_firestore
from app.strategies import get_all_strategies
from app.config.universe import UNIVERSE
import os, pytz

load_dotenv()
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

NY = pytz.timezone("America/New_York")
OPEN = datetime.strptime("09:30", "%H:%M").time()
CUTOFF = datetime.strptime("09:45", "%H:%M").time()

def handle_msg(msgs: List[EquityAgg]):
    db = get_firestore()

    for m in msgs:
        try:
            dt_utc = datetime.fromtimestamp(m.end_timestamp / 1000, tz=timezone.utc)
            dt_ny = dt_utc.astimezone(NY)
            in_open = OPEN <= dt_ny.time() <= CUTOFF

            candle = {
                "ev": m.event_type,
                "sym": m.symbol,               # ex: AM.I:SPX
                "op": m.official_open_price,
                "o": m.open,
                "c": m.close,
                "h": m.high,
                "l": m.low,
                "s": m.start_timestamp,
                "e": m.end_timestamp,
                "utc_time": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "day": dt_utc.strftime("%Y-%m-%d"),
                "in_opening_range": in_open
            }

            doc_id = f"{m.symbol}_{m.end_timestamp}"
            db.collection("ohlc_1m").document(doc_id).set(candle)

            # 09:45 NY → calcul du range pour CE symbole uniquement
            if dt_ny.time().strftime("%H:%M") == "09:45":
                day_str = dt_ny.strftime("%Y-%m-%d")
                log_to_firestore(f"🕒 09:45 NY → Calcul du range {m.symbol} pour {day_str}")
                calculate_and_store_opening_range(day=day_str, symbol=m.symbol)

            # Exécution des stratégies hors opening range
            if not in_open and UNIVERSE.get(m.symbol, {}).get("active", False):
                for strategy_fn in get_all_strategies():
                    try:
                        strategy_fn(candle)  # ↓ la stratégie lira symbol → instrument
                    except Exception as e:
                        log_to_firestore(f"❌ Erreur stratégie {strategy_fn.__name__} ({m.symbol}) : {e}", level="ERROR")

        except Exception as e:
            log_to_firestore(f"⚠️ Erreur traitement WS ({getattr(m,'symbol','?')}): {e}", level="ERROR")

def start_polygon_ws():
    client = WebSocketClient(api_key=POLYGON_API_KEY, feed=Feed.RealTime, market=Market.Indices)
    symbols = [sym for sym, cfg in UNIVERSE.items() if cfg.get("active")]
    # Multi-subscribe
    for sym in symbols:
        client.subscribe(sym)
    Thread(target=client.run, args=(handle_msg,), daemon=True).start()
