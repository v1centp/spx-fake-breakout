from datetime import datetime, timezone
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price,
    calculate_sl_tp,
    compute_position_size,
    execute_trade
)

STRATEGY_KEY = "sp500_mean_revert"
RISK_CHF = 50

def process(candle):
    db = get_firestore()
    today = candle["day"]

    # 📅 Heure NY
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()
    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    # ⚙️ Active ?
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # 🧮 Range
    rd = db.collection("opening_range").document(today).get().to_dict()
    if not rd or rd.get("status") != "ready":
        return
    high_15, low_15 = rd["high"], rd["low"]

    # 🚦 Condition entrée
    o, c = candle["o"], candle["c"]
    direction = None
    if o < low_15 and low_15 <= c <= high_15:
        direction = "LONG"
        sl_ref = min([entry.to_dict()["l"] for entry in 
                      db.collection("ohlc_1m").where("day", "==", today).stream()])
    elif o > high_15 and low_15 <= c <= high_15:
        direction = "SHORT"
        sl_ref = max([entry.to_dict()["h"] for entry in 
                      db.collection("ohlc_1m").where("day", "==", today).stream()])
    else:
        return

    # 🚫 Vérifier n’avoir pas dépassé 5 trades/jour
    trades = list(db.collection("trading_days").document(today).collection("trades")
                 .where("strategy", "==", STRATEGY_KEY).stream())
    if len(trades) >= 5:
        log_to_firestore(f"[{STRATEGY_KEY}] Limite 5 trades atteinte.", level="TRADING")
        return

    # ❌ Empêcher same-direction consécutif immédiatement
    if trades and trades[-1].to_dict().get("direction") == direction:
        log_to_firestore(f"[{STRATEGY_KEY}] Même direction déjà prise précédemment.", level="TRADING")
        return

    entry = get_entry_price()
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref, direction, tp_ratio=1.75)
    if risk_per_unit == 0:
        log_to_firestore(f"[{STRATEGY_KEY}] Risque nul → skip", level="ERROR"); return

    units = compute_position_size(risk_per_unit, RISK_CHF)
    if units < 0.1:
        log_to_firestore(f"[{STRATEGY_KEY}] Position trop petite ({units})", level="ERROR"); return

    try:
        executed_units = execute_trade(entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"[{STRATEGY_KEY}] Trade {direction} exécuté ({executed_units})", level="TRADING")

        db.collection("trading_days").document(today).collection("trades").document().set({
            "strategy": STRATEGY_KEY,
            "entry": entry,
            "sl": sl_price,
            "tp": tp_price,
            "direction": direction,
            "units": executed_units,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        log_to_firestore(f"[{STRATEGY_KEY}] Erreur exéc ordre: {e}", level="ERROR")
