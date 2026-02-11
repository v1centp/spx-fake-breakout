import math
import threading
import time
from datetime import datetime, timezone, timedelta
import pytz
from app.services.firebase import get_firestore
from app.services import oanda_service, kraken_service
from app.services.oanda_service import DECIMALS_BY_INSTRUMENT
from app.services.kraken_service import DECIMALS_BY_PAIR
from app.services.log_service import log_to_firestore, log_trade_event
from app.config.universe import UNIVERSE
from app.config.instrument_map import INSTRUMENT_MAP

POLL_INTERVAL = 30  # seconds

_open_trades = []  # list of (doc_ref, trade_id_value, broker)

# Reverse mapping: instrument -> session config
INSTRUMENT_SESSION = {cfg["instrument"]: cfg["session"] for cfg in UNIVERSE.values()}

# Forex instruments (from instrument_map)
FOREX_INSTRUMENTS = {cfg["oanda"] for cfg in INSTRUMENT_MAP.values() if "oanda" in cfg}


def _get_decimals(instrument: str, broker: str) -> int:
    """Get decimals for an instrument based on broker."""
    if broker == "kraken":
        return DECIMALS_BY_PAIR.get(instrument, 2)
    return DECIMALS_BY_INSTRUMENT.get(instrument, 5)


def _get_latest_price(instrument: str, broker: str) -> float:
    if broker == "kraken":
        return kraken_service.get_latest_price(instrument)
    return oanda_service.get_latest_price(instrument)


def _load_open_trades():
    """Load all trades with outcome == 'open' from Firestore."""
    db = get_firestore()
    trades = []

    for doc in db.collection_group("trades").where("outcome", "==", "open").stream():
        data = doc.to_dict()
        broker = data.get("broker", "oanda")
        # Use generic trade_id field, fallback to oanda_trade_id
        trade_id_val = data.get("trade_id") or data.get("oanda_trade_id")
        if trade_id_val:
            trades.append((doc.reference, trade_id_val, broker))

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


def _should_close_before_weekend(instrument: str, broker: str) -> bool:
    """Return True if it's Friday after 20:55 UTC and instrument is forex (not crypto)."""
    if broker == "kraken":
        return False  # Crypto trades 24/7, no weekend close
    if instrument not in FOREX_INSTRUMENTS:
        return False
    now_utc = datetime.now(timezone.utc)
    # Friday = 4, close at 20:55 UTC (5 min before market close)
    return now_utc.weekday() == 4 and now_utc.hour >= 20 and now_utc.minute >= 55


def _close_trade_broker(trade_id_val: str, trade_data: dict, broker: str, units=None):
    """Close a trade via the appropriate broker."""
    if broker == "kraken":
        instrument = trade_data.get("instrument", "")
        direction = trade_data.get("direction", "LONG")
        side = "buy" if direction == "LONG" else "sell"
        return kraken_service.close_trade(trade_id_val, pair=instrument, side=side, volume=units)
    return oanda_service.close_trade(trade_id_val, units=units)


def _modify_sl_broker(trade_id_val: str, new_sl: float, instrument: str, broker: str):
    """Modify SL via the appropriate broker."""
    if broker == "kraken":
        return kraken_service.modify_trade_sl(trade_id_val, new_sl, instrument)
    return oanda_service.modify_trade_sl(trade_id_val, new_sl, instrument)


def _get_trade_details_broker(trade_id_val: str, broker: str):
    """Get trade details via the appropriate broker."""
    if broker == "kraken":
        return kraken_service.get_trade_details(trade_id_val)
    return oanda_service.get_trade_details(trade_id_val)


def _auto_close_trade(doc_ref, trade_id_val: str, trade_data: dict, broker: str) -> bool:
    """Close a trade near session end or before weekend for forex. Returns True if closed."""
    instrument = trade_data.get("instrument")
    if not instrument:
        return False
    if broker == "kraken":
        return False  # No auto-close for crypto
    if not _should_auto_close(instrument) and not _should_close_before_weekend(instrument, broker):
        return False

    try:
        response = _close_trade_broker(trade_id_val, trade_data, broker)
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
            f"[TradeTracker] Trade {trade_id_val} auto-closed: PnL={realized_pl}",
            level="TRADING"
        )
        return True
    except Exception as e:
        log_to_firestore(
            f"[TradeTracker] Auto-close error on trade {trade_id_val}: {e}",
            level="ERROR"
        )
        return False


def _force_close_trade(doc_ref, trade_id_val: str, trade_data: dict, reason: str, broker: str) -> bool:
    """Force close a trade (e.g. max hold time expired). Returns True if closed."""
    try:
        response = _close_trade_broker(trade_id_val, trade_data, broker)
        realized_pl = 0.0
        if broker == "oanda":
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
            f"[TradeTracker] Trade {trade_id_val} force-closed ({reason}): PnL={realized_pl}",
            level="TRADING"
        )
        return True
    except Exception as e:
        log_to_firestore(
            f"[TradeTracker] Force-close error on trade {trade_id_val}: {e}",
            level="ERROR"
        )
        return False


def _get_be_offset(instrument: str, broker: str) -> float:
    """Return a small price offset for breakeven SL, ensuring a tiny locked-in profit."""
    decimals = _get_decimals(instrument, broker)
    # CFDs (1-2 decimals): 0.1, JPY pairs (3 decimals): 0.01, other forex (5 decimals): 0.0001
    offsets = {1: 0.1, 2: 0.1, 3: 0.01, 5: 0.0001}
    return offsets.get(decimals, 10 ** -(decimals))


def _check_breakeven(doc_ref, trade_id_val: str, broker: str):
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

        current_price = _get_latest_price(instrument, broker)

        if direction == "LONG":
            profit = current_price - fill_price
        else:
            profit = fill_price - current_price

        if profit >= risk * 0.5:
            offset = _get_be_offset(instrument, broker)
            if direction == "LONG":
                be_price = fill_price + offset
            else:
                be_price = fill_price - offset

            _modify_sl_broker(trade_id_val, be_price, instrument, broker)
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
                f"[TradeTracker] Breakeven applied on trade {trade_id_val}: "
                f"SL moved from {sl} to {be_price} (profit={profit:.2f}, risk={risk:.2f})",
                level="TRADING"
            )
    except Exception as e:
        log_to_firestore(
            f"[TradeTracker] Breakeven check error on trade {trade_id_val}: {e}",
            level="ERROR"
        )


def _check_scaling_out(doc_ref, trade_id_val: str, trade_data: dict, broker: str):
    """Scaling-out for ichimoku: TP1=1R (close 50%), TP2=2R (close 25%), TP3=6R (broker TP)."""
    try:
        scaling_step = trade_data.get("scaling_step", 0)
        if scaling_step >= 2:
            return  # TP1+TP2 done; TP3 (6R) handled by broker TP

        fill_price = float(trade_data.get("fill_price", 0))
        risk_r = float(trade_data.get("risk_r", 0))
        direction = trade_data.get("direction")
        instrument = trade_data.get("instrument")
        initial_units = abs(float(trade_data.get("initial_units", 0)))
        step = float(trade_data.get("step", 1))
        decimals = _get_decimals(instrument, broker)

        if not all([fill_price, risk_r, direction, instrument, initial_units]):
            return

        current_price = _get_latest_price(instrument, broker)

        if direction == "LONG":
            profit = current_price - fill_price
        else:
            profit = fill_price - current_price

        profit_r = profit / risk_r if risk_r else 0

        if profit_r >= 1.0 and scaling_step == 0:
            # TP1: close 50%, SL -> breakeven
            units_to_close = max(math.floor(initial_units * 0.5 / step) * step, step)
            expected_tp1 = fill_price + risk_r if direction == "LONG" else fill_price - risk_r

            close_resp = _close_trade_broker(trade_id_val, trade_data, broker, units=units_to_close)
            actual_price = 0
            if broker == "oanda":
                actual_price = float(close_resp.get("orderFillTransaction", {}).get("price", 0))
            else:
                actual_price = current_price  # Kraken doesn't return fill price in same format
            slippage = round(actual_price - expected_tp1, decimals) if actual_price else None

            offset = _get_be_offset(instrument, broker)
            be_price = fill_price + offset if direction == "LONG" else fill_price - offset
            _modify_sl_broker(trade_id_val, be_price, instrument, broker)

            doc_ref.update({
                "scaling_step": 1,
                "sl": be_price,
                "sl_original": trade_data.get("sl"),
                "breakeven_applied": True,
                "tp1_fill_price": actual_price or None,
                "tp1_slippage": slippage,
            })

            slip_str = f" (slippage: {slippage})" if slippage else ""
            log_trade_event(doc_ref, "SCALING_TP1",
                f"TP1 @ {actual_price} (attendu {expected_tp1:.{decimals}f}){slip_str}: "
                f"{units_to_close} units fermees, SL -> {be_price}", {
                    "units_closed": units_to_close,
                    "fill_price": actual_price,
                    "expected_price": round(expected_tp1, decimals),
                    "slippage": slippage,
                    "sl_new": be_price,
                    "profit_r": round(profit_r, 2),
                })
            log_to_firestore(
                f"[TradeTracker] Scaling TP1 on {trade_id_val}: "
                f"@ {actual_price}{slip_str}, {units_to_close}/{initial_units} units, SL -> {be_price}",
                level="TRADING"
            )

        elif profit_r >= 2.0 and scaling_step == 1:
            # TP2: close 25% of original, SL -> +1R
            units_to_close = max(math.floor(initial_units * 0.25 / step) * step, step)
            expected_tp2 = fill_price + 2 * risk_r if direction == "LONG" else fill_price - 2 * risk_r

            close_resp = _close_trade_broker(trade_id_val, trade_data, broker, units=units_to_close)
            actual_price = 0
            if broker == "oanda":
                actual_price = float(close_resp.get("orderFillTransaction", {}).get("price", 0))
            else:
                actual_price = current_price
            slippage = round(actual_price - expected_tp2, decimals) if actual_price else None

            if direction == "LONG":
                new_sl = fill_price + risk_r
            else:
                new_sl = fill_price - risk_r
            _modify_sl_broker(trade_id_val, new_sl, instrument, broker)

            doc_ref.update({
                "scaling_step": 2,
                "sl": new_sl,
                "tp2_fill_price": actual_price or None,
                "tp2_slippage": slippage,
            })

            slip_str = f" (slippage: {slippage})" if slippage else ""
            log_trade_event(doc_ref, "SCALING_TP2",
                f"TP2 @ {actual_price} (attendu {expected_tp2:.{decimals}f}){slip_str}: "
                f"{units_to_close} units fermees, SL -> {new_sl}", {
                    "units_closed": units_to_close,
                    "fill_price": actual_price,
                    "expected_price": round(expected_tp2, decimals),
                    "slippage": slippage,
                    "sl_new": new_sl,
                    "profit_r": round(profit_r, 2),
                })
            log_to_firestore(
                f"[TradeTracker] Scaling TP2 on {trade_id_val}: "
                f"@ {actual_price}{slip_str}, {units_to_close}/{initial_units} units, SL -> {new_sl}",
                level="TRADING"
            )

    except Exception as e:
        log_to_firestore(
            f"[TradeTracker] Scaling check error on trade {trade_id_val}: {e}",
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
            for doc_ref, trade_id_val, broker in _open_trades:
                try:
                    details = _get_trade_details_broker(trade_id_val, broker)
                except Exception as e:
                    log_to_firestore(
                        f"[TradeTracker] Error fetching trade {trade_id_val} [{broker}]: {e}",
                        level="ERROR"
                    )
                    still_open.append((doc_ref, trade_id_val, broker))
                    continue

                if details["state"] == "CLOSED":
                    realized_pl = float(details["realizedPL"])
                    trade_data = doc_ref.get().to_dict() or {}
                    scaling_step = trade_data.get("scaling_step", 0)
                    tp_filled = details.get("tp_filled", False)
                    sl_filled = details.get("sl_filled", False)
                    close_price = float(details["averageClosePrice"]) if details.get("averageClosePrice") else None
                    instrument = trade_data.get("instrument")
                    decimals = _get_decimals(instrument, broker) if instrument else 5

                    # Determine expected close price & slippage
                    if tp_filled:
                        expected_price = float(trade_data.get("tp", 0))
                    elif sl_filled:
                        expected_price = float(trade_data.get("sl", 0))
                    else:
                        expected_price = None
                    slippage = round(close_price - expected_price, decimals) if (close_price and expected_price) else None

                    if scaling_step >= 2:
                        outcome = "win"
                        close_reason = "TP3" if tp_filled else "SL +1R (apres TP2)"
                    elif scaling_step == 1:
                        outcome = "win"
                        close_reason = "TP (apres TP1)" if tp_filled else "BE SL (apres TP1)"
                    elif trade_data.get("breakeven_applied"):
                        if sl_filled:
                            outcome = "breakeven"
                            close_reason = "BE SL"
                        else:
                            outcome = _determine_outcome(realized_pl)
                            close_reason = "TP" if tp_filled else "SL"
                    else:
                        outcome = _determine_outcome(realized_pl)
                        close_reason = "TP" if tp_filled else "SL" if sl_filled else "close"

                    currency = "USD" if broker == "kraken" else "CHF"
                    update_data = {
                        "outcome": outcome,
                        "close_reason": close_reason,
                        "realized_pnl": realized_pl,
                        "close_time": datetime.now().isoformat(),
                    }
                    if close_price:
                        update_data["close_price"] = close_price
                    if slippage is not None:
                        update_data["close_slippage"] = slippage
                    doc_ref.update(update_data)

                    slip_str = f" (slippage: {slippage})" if slippage else ""
                    price_str = f" @ {close_price}" if close_price else ""
                    log_trade_event(doc_ref, "CLOSED",
                        f"Trade cloture: {close_reason}{price_str}{slip_str} — {outcome} (PnL: {realized_pl:.2f} {currency})", {
                        "outcome": outcome,
                        "close_reason": close_reason,
                        "close_price": close_price,
                        "slippage": slippage,
                        "realized_pnl": round(realized_pl, 2),
                        "instrument": instrument,
                        "direction": trade_data.get("direction"),
                        "scaling_step": scaling_step,
                        "breakeven_applied": trade_data.get("breakeven_applied"),
                        "broker": broker,
                    })

                    log_to_firestore(
                        f"[TradeTracker] Trade {trade_id_val} closed: {close_reason}{price_str}{slip_str} — {outcome} (PnL: {realized_pl:.2f})",
                        level="TRADING"
                    )
                else:
                    # --- Auto-close near session end (OANDA only) ---
                    trade_data = doc_ref.get().to_dict() or {}
                    if _auto_close_trade(doc_ref, trade_id_val, trade_data, broker):
                        continue

                    # --- Max hold time (news trading) ---
                    max_hold = trade_data.get("max_hold_until")
                    if max_hold:
                        max_hold_dt = datetime.fromisoformat(max_hold)
                        if datetime.now(timezone.utc) >= max_hold_dt:
                            _force_close_trade(doc_ref, trade_id_val, trade_data, "max_hold_expired", broker)
                            continue

                    # --- Position management: scaling or breakeven ---
                    if trade_data.get("scaling_step") is not None:
                        _check_scaling_out(doc_ref, trade_id_val, trade_data, broker)
                    else:
                        _check_breakeven(doc_ref, trade_id_val, broker)
                    still_open.append((doc_ref, trade_id_val, broker))

            _open_trades = still_open

        except Exception as e:
            log_to_firestore(f"[TradeTracker] Poll error: {e}", level="ERROR")

        time.sleep(POLL_INTERVAL)


def start():
    thread = threading.Thread(target=_poll_loop, daemon=True)
    thread.start()
    log_to_firestore("[TradeTracker] Background tracker started", level="INFO")
