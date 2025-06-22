from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)
from datetime import datetime, timezone
import pytz

STRATEGY_KEY = "sp500_fake_breakout_soft"
RISK_CHF = 50

def process(candle):
    db = get_firestore()
    today = candle["day"]
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()

    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    range_data = db.collection("opening_range").document(today).get().to_dict()
    if not range_data or range_data.get("status") != "ready":
        return

    executed_key = "executed_soft"
    trade_doc = db.collection("trading_days").document(today).get()
    if trade_doc.exists and trade_doc.to_dict().get(executed_key, False):
        log_to_firestore(f"🔁 [{STRATEGY_KEY}] Déjà exécutée aujourd'hui.", level="TRADING")
        return

    high_15, low_15 = range_data["high"], range_data["low"]
    range_size = range_data["range_size"]

    direction, breakout = None, None
    if candle["h"] > high_15 and low_15 <= candle["c"] <= high_15:
        breakout = candle["h"] - high_15
        if breakout >= 0.15 * range_size:
            direction = "SHORT"
    elif candle["l"] < low_15 and low_15 <= candle["c"] <= high_15:
        breakout = low_15 - candle["l"]
        if breakout >= 0.15 * range_size:
            direction = "LONG"

    if not direction:
        return

    log_to_firestore(f"🔍 [{STRATEGY_KEY}] Signal {direction} détecté", level="TRADING")

    try:
        entry = get_entry_price()
    except Exception as e:
        log_to_firestore(f"⛔ [{STRATEGY_KEY}] Erreur prix OANDA : {e}", level="ERROR")
        return

    spread_factor = entry / candle["c"]
    sl_ref = low_15 if direction == "LONG" else high_15
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref * spread_factor, direction)

    if risk_per_unit == 0:
        return

    units = compute_position_size(risk_per_unit)
    if units < 0.1:
        return

    try:
        executed = execute_trade(entry, sl_price, tp_price, units, direction)
        db.collection("trading_days").document(today).set({
            executed_key: True,
            "entry": entry,
            "sl": sl_price,
            "tp": tp_price,
            "direction": direction,
            "units": executed,
            "timestamp": datetime.now().isoformat()
        }, merge=True)
        log_to_firestore(f"✅ [{STRATEGY_KEY}] Ordre exécuté à {entry}", level="TRADING")
    except Exception as e:
        log_to_firestore(f"⛔ [{STRATEGY_KEY}] Erreur exécution : {e}", level="ERROR")
