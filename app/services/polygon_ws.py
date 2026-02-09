# app/services/polygon_ws.py
from massive import WebSocketClient
from massive.websocket.models import Feed, Market
from threading import Thread
import time
from app.services.firebase import get_firestore
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from app.services.range_manager import calculate_and_store_opening_range
from app.services.log_service import log_to_firestore
from app.strategies import get_all_strategies
from app.config.universe import UNIVERSE
import os, pytz

load_dotenv()
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

def _parse_hhmm(s):
    h, m = s.split(":"); return int(h), int(m)

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
            _ws_status["last_msg"] = datetime.now(timezone.utc).isoformat()
            dt_utc = datetime.fromtimestamp(m.end_timestamp/1000, tz=timezone.utc)
            sym = m.symbol  # <-- garder EXACTEMENT le symbole WS partout

            # horaires par symbole (si inconnu → défaut US)
            tz, oh, om, or_min, th, tm = _session_for(sym)
            dt_local = dt_utc.astimezone(tz)

            open_start = dt_local.replace(hour=oh, minute=om, second=0, microsecond=0)
            open_end   = open_start + timedelta(minutes=or_min)
            trade_end  = dt_local.replace(hour=th, minute=tm, second=0, microsecond=0)

            in_open = open_start <= dt_local <= open_end

            candle = {
                "ev": m.event_type, "sym": sym,
                "op": m.official_open_price,
                "o": m.open, "c": m.close, "h": m.high, "l": m.low,
                "s": m.start_timestamp, "e": m.end_timestamp,
                "utc_time": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "day": dt_utc.strftime("%Y-%m-%d"),
                "in_opening_range": in_open,
            }

            doc_id = f"{sym}_{m.end_timestamp}"
            db.collection("ohlc_1m").document(doc_id).set(candle)
            # log debug utile:
            # print(f"✅ Stored {doc_id} (in_open={in_open})")

            # Fin de fenêtre d’ouverture locale → calcule le range pour CE symbole
            if dt_local.strftime("%H:%M") == open_end.strftime("%H:%M"):
                day_str = dt_local.strftime("%Y-%m-%d")
                log_to_firestore(f"🕒 {sym} {open_end.strftime('%H:%M %Z')} → calc range {day_str}")
                calculate_and_store_opening_range(day=day_str, symbol=sym)

            # Exécuter stratégies hors opening range et avant fin de session
            if (not in_open) and (dt_local <= trade_end) and UNIVERSE.get(sym, {}).get("active", False):
                for fn in get_all_strategies():
                    try:
                        fn(candle)
                    except Exception as e:
                        log_to_firestore(f"❌ Strat {fn.__name__} ({sym}) : {e}", level="ERROR")

        except Exception as e:
            log_to_firestore(f"⚠️ WS error ({getattr(m,'symbol','?')}) : {e}", level="ERROR")

_ws_status = {"connected": False, "last_msg": None, "reconnects": 0, "market_open": False}

_NY = pytz.timezone("America/New_York")
MAX_BACKOFF = 60  # seconds
OFF_HOURS_SLEEP = 300  # 5 min between checks when market is closed


def _is_market_open() -> bool:
    """US indices: Mon-Fri ~09:00-16:05 ET (connect a bit early, linger a bit after close)."""
    now = datetime.now(timezone.utc).astimezone(_NY)
    # Weekend
    if now.weekday() >= 5:
        return False
    # Outside 09:00 - 16:05 ET
    t = now.hour * 60 + now.minute
    return 9 * 60 <= t <= 16 * 60 + 5


def get_ws_status():
    status = _ws_status.copy()
    status["market_open"] = _is_market_open()
    return status


def _run_with_reconnect():
    global _ws_status
    backoff = 1

    while True:
        # Wait for market hours before connecting
        if not _is_market_open():
            _ws_status["connected"] = False
            _ws_status["market_open"] = False
            time.sleep(OFF_HOURS_SLEEP)
            continue

        _ws_status["market_open"] = True

        try:
            client = WebSocketClient(api_key=POLYGON_API_KEY, feed=Feed.RealTime, market=Market.Indices)

            symbols = [sym for sym, cfg in UNIVERSE.items() if cfg.get("active")]
            if not symbols:
                log_to_firestore("[PolygonWS] Aucun symbole actif dans UNIVERSE", level="ERROR")
                time.sleep(30)
                continue

            for sym in symbols:
                client.subscribe('AM.' + sym)

            _ws_status["connected"] = True
            log_to_firestore(
                f"[PolygonWS] Connecte — {len(symbols)} symbole(s): {', '.join(symbols)}",
                level="INFO"
            )
            backoff = 1  # reset on successful connect

            client.run(handle_msg)

            # client.run returned — connection closed
            _ws_status["connected"] = False

            # If market just closed, don't log as error
            if not _is_market_open():
                log_to_firestore("[PolygonWS] Marche ferme, WS deconnecte", level="INFO")
                continue

            log_to_firestore("[PolygonWS] Connexion fermee, reconnexion...", level="ERROR")

        except Exception as e:
            _ws_status["connected"] = False
            _ws_status["reconnects"] += 1

            # Only log errors during market hours
            if _is_market_open():
                log_to_firestore(
                    f"[PolygonWS] Erreur WS: {e} — reconnexion dans {backoff}s (tentative #{_ws_status['reconnects']})",
                    level="ERROR"
                )

            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)


def start_polygon_ws():
    Thread(target=_run_with_reconnect, daemon=True).start()
