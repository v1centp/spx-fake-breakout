import math
import threading
import time
from datetime import datetime, timezone, timedelta
import pytz
from app.services.firebase import get_firestore
from app.services import oanda_service
from app.services.oanda_service import DECIMALS_BY_INSTRUMENT
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


def _force_close_trade(doc_ref, oanda_trade_id: str, trade_data: dict, reason: str) -> bool:
    """Force close a trade (e.g. max hold time expired). Returns True if closed."""
    try:
        response = oanda_service.close_trade(oanda_trade_id)
        realized_pl = 0.0
        try:
            fill_tx = response.get("orderFillTransaction", {})
            realized_pl = float(fill_tx.get("pl", 0))
        except Exception:
            pass

        doc_ref.update({
            "outcome": reason,
            "realized_pnl": realized_pl,
            "close_time": datetime.now().isoformat(),
        })

        instrument = trade_data.get("instrument", "unknown")
        log_trade_event(doc_ref, "FORCE_CLOSED", f"Trade force-closed: {reason} (PnL: {realized_pl})", {
            "outcome": reason,
            "realized_pnl": realized_pl,
            "instrument": instrument,
        })

        log_to_firestore(
            f"[TradeTracker] Trade {oanda_trade_id} force-closed ({reason}): PnL={realized_pl}",
            level="TRADING"
        )
        return True
    except Exception as e:
        log_to_firestore(
            f"[TradeTracker] Force-close error on trade {oanda_trade_id}: {e}",
            level="ERROR"
        )
        return False


def _get_be_offset(instrument: str) -> float:
    """Return a small price offset for breakeven SL, ensuring a tiny locked-in profit."""
    decimals = DECIMALS_BY_INSTRUMENT.get(instrument, 2)
    # CFDs (1-2 decimals): 0.1, JPY pairs (3 decimals): 0.01, other forex (5 decimals): 0.0001
    offsets = {1: 0.1, 2: 0.1, 3: 0.01, 5: 0.0001}
    return offsets.get(decimals, 10 ** -(decimals))


def _check_breakeven(doc_ref, oanda_trade_id: str):
    """Move SL to breakeven (fill_price + small offset) when trade reaches +0.5R profit."""
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

        if profit >= risk * 0.5:
            offset = _get_be_offset(instrument)
            if direction == "LONG":
                be_price = fill_price + offset
            else:
                be_price = fill_price - offset

            oanda_service.modify_trade_sl(oanda_trade_id, be_price, instrument)
            doc_ref.update({
                "breakeven_applied": True,
                "sl_original": sl,
                "sl": be_price,
            })
            log_trade_event(doc_ref, "BREAKEVEN", f"SL deplace au breakeven: {sl} -> {be_price}", {
                "sl_original": sl,
                "sl_new": be_price,
                "profit_at_trigger": round(profit, 2),
                "risk": round(risk, 2),
            })
            log_to_firestore(
                f"[TradeTracker] Breakeven applied on trade {oanda_trade_id}: "
                f"SL moved from {sl} to {be_price} (profit={profit:.2f}, risk={risk:.2f})",
                level="TRADING"
            )
    except Exception as e:
        log_to_firestore(
            f"[TradeTracker] Breakeven check error on trade {oanda_trade_id}: {e}",
            level="ERROR"
        )


def _check_scaling_out(doc_ref, oanda_trade_id: str, trade_data: dict):
    """Scaling-out for ichimoku: TP1=1R (close 50%), TP2=2R (close 25%), TP3=6R (OANDA TP)."""
    try:
        scaling_step = trade_data.get("scaling_step", 0)
        if scaling_step >= 2:
            return  # TP1+TP2 done; TP3 (6R) handled by OANDA TP

        fill_price = float(trade_data.get("fill_price", 0))
        risk_r = float(trade_data.get("risk_r", 0))
        direction = trade_data.get("direction")
        instrument = trade_data.get("instrument")
        initial_units = abs(float(trade_data.get("initial_units", 0)))
        step = float(trade_data.get("step", 1))

        if not all([fill_price, risk_r, direction, instrument, initial_units]):
            return

        current_price = oanda_service.get_latest_price(instrument)

        if direction == "LONG":
            profit = current_price - fill_price
        else:
            profit = fill_price - current_price

        profit_r = profit / risk_r if risk_r else 0

        if profit_r >= 1.0 and scaling_step == 0:
            # TP1: close 50%, SL -> breakeven
            units_to_close = max(math.floor(initial_units * 0.5 / step) * step, step)
            oanda_service.close_trade(oanda_trade_id, units=units_to_close)

            offset = _get_be_offset(instrument)
            be_price = fill_price + offset if direction == "LONG" else fill_price - offset
            oanda_service.modify_trade_sl(oanda_trade_id, be_price, instrument)

            doc_ref.update({
                "scaling_step": 1,
                "sl": be_price,
                "sl_original": trade_data.get("sl"),
                "breakeven_applied": True,
            })

            log_trade_event(doc_ref, "SCALING_TP1",
                f"TP1 atteint ({profit_r:.1f}R): {units_to_close} units fermees, SL -> {be_price}", {
                    "units_closed": units_to_close,
                    "sl_new": be_price,
                    "profit_r": round(profit_r, 2),
                })
            log_to_firestore(
                f"[TradeTracker] Scaling TP1 on {oanda_trade_id}: "
                f"closed {units_to_close}/{initial_units} units, SL -> {be_price}",
                level="TRADING"
            )

        elif profit_r >= 2.0 and scaling_step == 1:
            # TP2: close 25% of original, SL -> +1R
            units_to_close = max(math.floor(initial_units * 0.25 / step) * step, step)
            oanda_service.close_trade(oanda_trade_id, units=units_to_close)

            if direction == "LONG":
                new_sl = fill_price + risk_r
            else:
                new_sl = fill_price - risk_r
            oanda_service.modify_trade_sl(oanda_trade_id, new_sl, instrument)

            doc_ref.update({
                "scaling_step": 2,
                "sl": new_sl,
            })

            log_trade_event(doc_ref, "SCALING_TP2",
                f"TP2 atteint ({profit_r:.1f}R): {units_to_close} units fermees, SL -> {new_sl}", {
                    "units_closed": units_to_close,
                    "sl_new": new_sl,
                    "profit_r": round(profit_r, 2),
                })
            log_to_firestore(
                f"[TradeTracker] Scaling TP2 on {oanda_trade_id}: "
                f"closed {units_to_close}/{initial_units} units, SL -> {new_sl}",
                level="TRADING"
            )

    except Exception as e:
        log_to_firestore(
            f"[TradeTracker] Scaling check error on trade {oanda_trade_id}: {e}",
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
                    trade_data = doc_ref.get().to_dict() or {}

                    if trade_data.get("breakeven_applied"):
                        # If BE was applied, distinguish BE SL hit from TP hit
                        # using original risk as reference
                        fill_price = float(trade_data.get("fill_price", 0))
                        sl_original = float(trade_data.get("sl_original", 0))
                        units = abs(float(trade_data.get("units", 0)))
                        risk_distance = abs(fill_price - sl_original)
                        est_risk_pnl = risk_distance * units if risk_distance and units else 50
                        # PnL < 25% of 1R => BE SL was hit (not TP)
                        if realized_pl < est_risk_pnl * 0.25:
                            outcome = "breakeven"
                        else:
                            outcome = _determine_outcome(realized_pl)
                    else:
                        outcome = _determine_outcome(realized_pl)

                    doc_ref.update({
                        "outcome": outcome,
                        "realized_pnl": realized_pl,
                        "close_time": datetime.now().isoformat(),
                    })

                    log_trade_event(doc_ref, "CLOSED", f"Trade cloture: {outcome} (PnL: {realized_pl:.2f} CHF)", {
                        "outcome": outcome,
                        "realized_pnl": round(realized_pl, 2),
                        "instrument": trade_data.get("instrument"),
                        "direction": trade_data.get("direction"),
                        "scaling_step": trade_data.get("scaling_step"),
                        "breakeven_applied": trade_data.get("breakeven_applied"),
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

                    # --- Max hold time (news trading) ---
                    max_hold = trade_data.get("max_hold_until")
                    if max_hold:
                        max_hold_dt = datetime.fromisoformat(max_hold)
                        if datetime.now(timezone.utc) >= max_hold_dt:
                            _force_close_trade(doc_ref, oanda_trade_id, trade_data, "max_hold_expired")
                            continue

                    # --- Position management: scaling or breakeven ---
                    if trade_data.get("scaling_step") is not None:
                        _check_scaling_out(doc_ref, oanda_trade_id, trade_data)
                    else:
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
