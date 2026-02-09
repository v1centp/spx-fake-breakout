# app/strategies/news_trading_strategy.py
from datetime import datetime, timezone, timedelta
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore, log_trade_event
from app.services.news_analyzer import _is_inverse_event
from app.services.shared_strategy_tools import (
    get_entry_price, compute_position_size, execute_trade
)
from app.services.oanda_service import DECIMALS_BY_INSTRUMENT

STRATEGY_KEY = "news_trading"
DEFAULT_RISK_CHF = 50
DEFAULT_SL_PIPS = 15
TP_RATIO = 2.0
MAX_HOLD_MINUTES = 30

# Pip values per instrument (1 pip = this many price units)
PIP_VALUES = {
    "EUR_USD": 0.0001,
    "GBP_USD": 0.0001,
    "USD_CHF": 0.0001,
    "EUR_GBP": 0.0001,
    "AUD_USD": 0.0001,
    "NZD_USD": 0.0001,
    "USD_CAD": 0.0001,
    "USD_JPY": 0.01,
    "EUR_JPY": 0.01,
    "GBP_JPY": 0.01,
}


def _determine_trade_direction(event: dict, surprise: dict, instrument: str) -> str | None:
    """
    Determine LONG or SHORT based on event surprise and instrument pair.

    Logic:
    - NFP beat + USD_CHF -> LONG (USD bullish, USD is base)
    - NFP beat + EUR_USD -> SHORT (USD bullish, USD is quote -> pair drops)
    - Unemployment beat + USD_CHF -> SHORT (inverse event: higher = bearish for USD)
    """
    direction_str = surprise.get("direction")
    if direction_str not in ("ABOVE", "BELOW"):
        return None

    surprise_bullish = direction_str == "ABOVE"

    # Flip for inverse events
    if _is_inverse_event(event["title"]):
        surprise_bullish = not surprise_bullish

    base, quote = instrument.split("_")
    event_currency = event["country"].upper()

    if event_currency == base:
        # Currency is base: bullish currency -> LONG, bearish -> SHORT
        return "LONG" if surprise_bullish else "SHORT"
    elif event_currency == quote:
        # Currency is quote: bullish currency -> SHORT (pair goes down), bearish -> LONG
        return "SHORT" if surprise_bullish else "LONG"

    return None


def execute_news_trade(
    event: dict,
    event_id: str,
    instrument: str,
    pre_analysis: dict,
    surprise: dict,
    decision: dict,
) -> dict:
    """
    Execute a news trade following the same pattern as ichimoku_strategy.

    Returns dict with status and trade details.
    """
    db = get_firestore()

    # Check strategy enabled
    strat_cfg = db.collection("config").document("strategies").get().to_dict() or {}
    if not strat_cfg.get(STRATEGY_KEY, False):
        log_to_firestore(f"[{STRATEGY_KEY}] Strategy disabled, skipping", level="INFO")
        return {"status": "SKIP", "reason": "Strategy disabled"}

    # Determine direction
    direction = decision.get("instrument_direction")
    if direction == "BULLISH":
        trade_direction = "LONG"
    elif direction == "BEARISH":
        trade_direction = "SHORT"
    else:
        trade_direction = _determine_trade_direction(event, surprise, instrument)

    if not trade_direction:
        log_to_firestore(
            f"[{STRATEGY_KEY}] Cannot determine direction for {event['title']} on {instrument}",
            level="INFO"
        )
        return {"status": "SKIP", "reason": "Cannot determine trade direction"}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check no existing trade for this event
    trade_id = f"{today}_{event_id}_{instrument}"
    existing = db.collection("strategies").document(STRATEGY_KEY) \
        .collection("trades").document(trade_id).get()
    if existing.exists:
        log_to_firestore(
            f"[{STRATEGY_KEY}] Trade already exists for {trade_id}",
            level="INFO"
        )
        return {"status": "SKIP", "reason": "Trade already taken for this event"}

    # Entry price
    try:
        entry = float(get_entry_price(instrument))
    except Exception as e:
        log_to_firestore(f"[{STRATEGY_KEY}] Price fetch error: {e}", level="ERROR")
        return {"status": "ERROR", "reason": f"Price fetch failed: {e}"}

    # SL/TP in pips
    pip_value = PIP_VALUES.get(instrument, 0.0001)
    sl_distance = DEFAULT_SL_PIPS * pip_value
    tp_distance = DEFAULT_SL_PIPS * TP_RATIO * pip_value
    decimals = DECIMALS_BY_INSTRUMENT.get(instrument, 5)

    if trade_direction == "LONG":
        sl_price = round(entry - sl_distance, decimals)
        tp_price = round(entry + tp_distance, decimals)
    else:
        sl_price = round(entry + sl_distance, decimals)
        tp_price = round(entry - tp_distance, decimals)

    risk_per_unit = sl_distance

    # Risk config
    settings = db.collection("config").document("settings").get().to_dict() or {}
    risk_chf = settings.get("risk_chf", DEFAULT_RISK_CHF)

    # Position sizing (step=1 for forex)
    units = compute_position_size(risk_per_unit, risk_chf, step=1, instrument=instrument)
    if units < 1:
        log_to_firestore(f"[{STRATEGY_KEY}] Position too small ({units})", level="ERROR")
        return {"status": "ERROR", "reason": f"Position too small: {units}"}

    # Execute
    try:
        result = execute_trade(instrument, entry, sl_price, tp_price, units, trade_direction, step=1)
        log_to_firestore(
            f"[{STRATEGY_KEY}] {trade_direction} {instrument} executed ({result['units']} units)",
            level="TRADING"
        )
    except Exception as e:
        log_to_firestore(f"[{STRATEGY_KEY}] Execution error: {e}", level="ERROR")
        return {"status": "ERROR", "reason": f"Execution failed: {e}"}

    # Max hold time
    max_hold_until = (datetime.now(timezone.utc) + timedelta(minutes=MAX_HOLD_MINUTES)).isoformat()

    # Save to Firestore
    trade_ref = db.collection("strategies").document(STRATEGY_KEY) \
        .collection("trades").document(trade_id)
    trade_data = {
        "strategy": STRATEGY_KEY,
        "instrument": instrument,
        "date": today,
        "entry": entry,
        "sl": sl_price,
        "tp": tp_price,
        "direction": trade_direction,
        "units": result["units"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": "open",
        "oanda_trade_id": result.get("oanda_trade_id"),
        "fill_price": result.get("fill_price"),
        "max_hold_until": max_hold_until,
        "event_title": event.get("title"),
        "event_country": event.get("country"),
        "surprise_direction": surprise.get("direction"),
        "surprise_magnitude": surprise.get("magnitude"),
        "surprise_actual": surprise.get("actual"),
        "surprise_forecast": surprise.get("forecast"),
        "gpt_bias": pre_analysis.get("bias"),
        "gpt_confidence": pre_analysis.get("confidence"),
        "gpt_analysis": pre_analysis.get("analysis"),
        "decision_reason": decision.get("reason"),
    }
    trade_ref.set(trade_data)

    log_trade_event(trade_ref, "OPENED", f"News trade {trade_direction} on {instrument}", {
        "entry": entry,
        "fill_price": result.get("fill_price"),
        "sl": sl_price,
        "tp": tp_price,
        "direction": trade_direction,
        "units": result["units"],
        "instrument": instrument,
        "oanda_trade_id": result.get("oanda_trade_id"),
        "event": event.get("title"),
        "surprise": surprise.get("direction"),
        "magnitude": surprise.get("magnitude"),
    })

    log_to_firestore(
        f"[{STRATEGY_KEY}] Trade {instrument} {trade_direction} @ {entry} "
        f"(SL: {sl_price}, TP: {tp_price}, hold until: {max_hold_until})",
        level="TRADING"
    )

    return {
        "status": "EXECUTED",
        "trade_id": trade_id,
        "instrument": instrument,
        "direction": trade_direction,
        "entry": entry,
        "sl": sl_price,
        "tp": tp_price,
        "units": result["units"],
        "oanda_trade_id": result.get("oanda_trade_id"),
        "max_hold_until": max_hold_until,
    }
