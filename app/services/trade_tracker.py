import threading
import time
from datetime import datetime, timezone, timedelta
import pytz
from app.services.firebase import get_firestore
from app.services import oanda_service
from app.services.log_service import log_to_firestore, log_trade_event
from app.config.universe import UNIVERSE
from app.config.instrument_map import INSTRUMENT_MAP

POLL_INTERVAL = 30  # seconds

_open_trades = []  # list of (doc_ref, oanda_trade_id)

# Reverse mapping: instrument -> session config
INSTRUMENT_SESSION = {cfg["instrument"]: cfg["session"] for cfg in UNIVERSE.values()}

# Forex instruments (from instrument_map)
FOREX_INSTRUMENTS = {cfg["oanda"] for cfg in INSTRUMENT_MAP.values()}


def _load_open_trades():
    """Load all trades with outcome == 'open' from Firestore."""
    db = get_firestore()
    trades = []

    for doc in db.collection_group("trades").where("outcome", "==", "open").stream():
        oanda_id = doc.to_dict().get("oanda_trade_id")
        if oanda_id:
            trades.append((doc.reference, oanda_id))

    return trades


def _determine_outcome(realized_pl: float) -> str:
    if realized_pl > 0:
        return "win"
    elif realized_pl < 0:
        return "loss"
    return "breakeven"


def _should_auto_close(instrument: str) -> bool:
    """Return True if we are within 5 minutes of the session trade_end for this instrument."""
    session = INSTRUMENT_SESSION.get(instrument)
    if not session:
        return False
    tz = pytz.timezone(session.get("tz", "America/New_York"))
    now_local = datetime.now(timezone.utc).astimezone(tz)
    th, tm = map(int, session.get("trade_end", "11:30").split(":"))
    trade_end = now_local.replace(hour=th, minute=tm, second=0, microsecond=0)
    return now_local >= trade_end - timedelta(minutes=5)


def _should_close_before_weekend(instrument: str) -> bool:
    """Return True if it's Friday after 20:55 UTC and instrument is forex."""
    if instrument not in FOREX_INSTRUMENTS:
        return False
    now_utc = datetime.now(timezone.utc)
    # Friday = 4, close at 20:55 UTC (5 min before market close)
    return now_utc.weekday() == 4 and now_utc.hour >= 20 and now_utc.minute >= 55


def _auto_close_trade(doc_ref, oanda_trade_id: str, trade_data: dict) -> bool:
    """Close a trade near session end or before weekend for forex. Returns True if closed."""
    instrument = trade_data.get("instrument")
    if not instrument:
        return False
    if not _should_auto_close(instrument) and not _should_close_before_weekend(instrument):
        return False

    try:
        response = oanda_service.close_trade(oanda_trade_id)
        realized_pl = 0.0
        try:
            fill_tx = response.get("orderFillTransaction", {})
            realized_pl = float(fill_tx.get("pl", 0))
        except Exception:
            pass

        doc_ref.update({
            "outcome": "auto_closed",
            "realized_pnl": realized_pl,
            "close_time": datetime.now().isoformat(),
        })

        log_trade_event(doc_ref, "AUTO_CLOSED", f"Trade auto-cloture avant fin de session (PnL: {realized_pl})", {
            "outcome": "auto_closed",
            "realized_pnl": realized_pl,
            "instrument": instrument,
        })

        log_to_firestore(
            f"[TradeTracker] Trade {oanda_trade_id} auto-closed: PnL={realized_pl}",
            level="TRADING"
        )
        return True
    except Exception as e:
        log_to_firestore(
            f"[TradeTracker] Auto-close error on trade {oanda_trade_id}: {e}",
            level="ERROR"
        )
        return False


def _check_breakeven(doc_ref, oanda_trade_id: str):
    """Move SL to breakeven (fill_price) when trade reaches +1R profit."""
    try:
        trade_data = doc_ref.get().to_dict()
        if not trade_data:
            return

        if trade_data.get("breakeven_applied"):
            return

        fill_price = trade_data.get("fill_price")
        sl = trade_data.get("sl")
        direction = trade_data.get("direction")
        instrument = trade_data.get("instrument")

        if not all([fill_price, sl, direction, instrument]):
            return

        fill_price = float(fill_price)
        sl = float(sl)
        risk = abs(fill_price - sl)
        if risk == 0:
            return

        current_price = oanda_service.get_latest_price(instrument)

        if direction == "LONG":
            profit = current_price - fill_price
        else:
            profit = fill_price - current_price

        if profit >= risk:
            oanda_service.modify_trade_sl(oanda_trade_id, fill_price, instrument)
            doc_ref.update({
                "breakeven_applied": True,
                "sl_original": sl,
                "sl": fill_price,
            })
            log_trade_event(doc_ref, "BREAKEVEN", f"SL deplace au breakeven: {sl} -> {fill_price}", {
                "sl_original": sl,
                "sl_new": fill_price,
                "profit_at_trigger": round(profit, 2),
                "risk": round(risk, 2),
            })
            log_to_firestore(
                f"[TradeTracker] Breakeven applied on trade {oanda_trade_id}: "
                f"SL moved from {sl} to {fill_price} (profit={profit:.2f}, risk={risk:.2f})",
                level="TRADING"
            )
    except Exception as e:
        log_to_firestore(
            f"[TradeTracker] Breakeven check error on trade {oanda_trade_id}: {e}",
            level="ERROR"
        )


def _poll_loop():
    global _open_trades

    while True:
        try:
            # Reload open trades each cycle to pick up new ones
            _open_trades = _load_open_trades()

            if _open_trades:
                log_to_firestore(
                    f"[TradeTracker] Tracking {len(_open_trades)} open trade(s)",
                    level="INFO"
                )

            still_open = []
            for doc_ref, oanda_trade_id in _open_trades:
                try:
                    details = oanda_service.get_trade_details(oanda_trade_id)
                except Exception as e:
                    log_to_firestore(
                        f"[TradeTracker] Error fetching trade {oanda_trade_id}: {e}",
                        level="ERROR"
                    )
                    still_open.append((doc_ref, oanda_trade_id))
                    continue

                if details["state"] == "CLOSED":
                    realized_pl = float(details["realizedPL"])
                    outcome = _determine_outcome(realized_pl)

                    doc_ref.update({
                        "outcome": outcome,
                        "realized_pnl": realized_pl,
                        "close_time": datetime.now().isoformat(),
                    })

                    log_trade_event(doc_ref, "CLOSED", f"Trade cloture: {outcome} (PnL: {realized_pl})", {
                        "outcome": outcome,
                        "realized_pnl": realized_pl,
                    })

                    log_to_firestore(
                        f"[TradeTracker] Trade {oanda_trade_id} closed: {outcome} (PnL: {realized_pl})",
                        level="TRADING"
                    )
                else:
                    # --- Auto-close near session end ---
                    trade_data = doc_ref.get().to_dict() or {}
                    if _auto_close_trade(doc_ref, oanda_trade_id, trade_data):
                        continue

                    # --- Breakeven logic at +1R ---
                    _check_breakeven(doc_ref, oanda_trade_id)
                    still_open.append((doc_ref, oanda_trade_id))

            _open_trades = still_open

        except Exception as e:
            log_to_firestore(f"[TradeTracker] Poll error: {e}", level="ERROR")

        time.sleep(POLL_INTERVAL)


def start():
    thread = threading.Thread(target=_poll_loop, daemon=True)
    thread.start()
    log_to_firestore("[TradeTracker] Background tracker started", level="INFO")
