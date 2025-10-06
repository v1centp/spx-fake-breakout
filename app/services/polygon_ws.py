# app/services/polygon_ws.py
from polygon import WebSocketClient
from polygon.websocket.models import Feed, Market, EquityAgg
from typing import List
from threading import Thread
from app.services.firebase import get_firestore
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from app.services.range_manager import calculate_and_store_opening_range
from app.services.log_service import log_to_firestore
from app.strategies import get_all_strategies
from app.config.universe import UNIVERSE
import os, pytz
from app.utils.symbols import normalize_symbol


load_dotenv()
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

def _parse_hhmm(s):  # "09:00" -> (9,0)
    h,m = s.split(":"); return int(h), int(m)

def _session_for(sym):
    s = UNIVERSE.get(sym, {}).get("session", {})
    tz = pytz.timezone(s.get("tz", "America/New_York"))
    oh, om = _parse_hhmm(s.get("open", "09:30"))
    or_minutes = int(s.get("or_minutes", 15))
    th, tm = _parse_hhmm(s.get("trade_end", "11:30"))
    return tz, oh, om, or_minutes, th, tm

def handle_msg(msgs):
    db = get_firestore()
    for m in msgs:
        try:
            dt_utc = datetime.fromtimestamp(m.end_timestamp/1000, tz=timezone.utc)
            sym = m.symbol
            tz, oh, om, or_min, th, tm = _session_for(sym)
            dt_local = dt_utc.astimezone(tz)

            open_start = dt_local.replace(hour=oh, minute=om, second=0, microsecond=0)
            open_end   = open_start + timedelta(minutes=or_min)      # fin de la fenêtre range
            trade_end  = dt_local.replace(hour=th, minute=tm, second=0, microsecond=0)

            in_open = open_start.time() <= dt_local.time() <= open_end.time()
            sym_raw = m.symbol
            sym = normalize_symbol(sym_raw)
            candle = {
                "ev": m.event_type, "sym": sym,
                "op": m.official_open_price,
                "o": m.open, "c": m.close, "h": m.high, "l": m.low,
                "s": m.start_timestamp, "e": m.end_timestamp,
                "utc_time": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "day": dt_utc.strftime("%Y-%m-%d"),
                "in_opening_range": in_open,
            }
            db.collection("ohlc_1m").document(f"{sym}_{m.end_timestamp}").set(candle)

            # À la fin de la fenêtre d’ouverture LOCALE → calcule le range pour CE symbole
            if dt_local.strftime("%H:%M") == open_end.strftime("%H:%M"):
                day_str = dt_local.strftime("%Y-%m-%d")
                log_to_firestore(f"🕒 {sym} {open_end.strftime('%H:%M %Z')} → calc range {day_str}")
                calculate_and_store_opening_range(day=day_str, symbol=sym)

            # Exécuter stratégies en dehors de la fenêtre range (mais avant trade_end)
            if not in_open and dt_local.time() <= trade_end.time() and UNIVERSE.get(sym,{}).get("active",False):
                for fn in get_all_strategies():
                    try:
                        fn(candle)
                    except Exception as e:
                        log_to_firestore(f"❌ Strat {fn.__name__} ({sym}) : {e}", level="ERROR")
        except Exception as e:
            log_to_firestore(f"⚠️ WS error ({getattr(m,'symbol','?')}) : {e}", level="ERROR")

def start_polygon_ws():
    client = WebSocketClient(api_key=POLYGON_API_KEY, feed=Feed.RealTime, market=Market.Indices)
    symbols = [sym for sym, cfg in UNIVERSE.items() if cfg.get("active")]
    # Multi-subscribe
    for sym in symbols:
        client.subscribe(sym)
    Thread(target=client.run, args=(handle_msg,), daemon=True).start()
