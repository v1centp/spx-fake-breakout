from datetime import datetime, timezone
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)

STRATEGY_KEY = "sp500_mean_revert"
RISK_CHF = 150

def process(candle):
    db = get_firestore()
    today = candle["day"]

    # ⏱️ Heure NY
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()
    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    # ⚙️ Activation stratégie
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # 📊 Récupération du range d'ouverture
    range_data = db.collection("opening_range").document(today).get().to_dict()
    if not range_data or range_data.get("status") != "ready":
        return

    high_15 = range_data["high"]
    low_15 = range_data["low"]
    o = candle["o"]
    c = candle["c"]
    candle_id = f"{candle['sym']}_{candle['e']}"

    direction = None
    sl_ref_polygon = None

    if o > high_15 and low_15 <= c <= high_15:
        direction = "SHORT"
        sl_ref_polygon = max(entry.to_dict()["h"] for entry in db.collection("ohlc_1m").where("day", "==", today).stream())
    elif o < low_15 and low_15 <= c <= high_15:
        direction = "LONG"
        sl_ref_polygon = min(entry.to_dict()["l"] for entry in db.collection("ohlc_1m").where("day", "==", today).stream())
    else:
        db.collection("ohlc_1m").document(candle_id).update({f"strategy_decisions.{STRATEGY_KEY}": "REJECT: conditions non remplies"})
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Conditions non remplies (open hors range, close dans range)", level="NO_TRADING")
        return

    db.collection("ohlc_1m").document(candle_id).update({f"strategy_decisions.{STRATEGY_KEY}": f"ACCEPT: signal {direction}"})

    # 🔁 Vérifie trade dans même direction déjà pris
    trades_same_dir = list(db.collection("trading_days")
        .document(today)
        .collection("trades")
        .where("strategy", "==", STRATEGY_KEY)
        .where("direction", "==", direction)
        .stream())

    if trades_same_dir:
        log_to_firestore(f"🔁 [{STRATEGY_KEY}] Trade {direction} déjà exécuté aujourd'hui.", level="TRADING")
        return

    log_to_firestore(f"[{STRATEGY_KEY}] 📌 Signal {direction} détecté : open hors range, close dans range", level="TRADING")

    try:
        entry = get_entry_price()
        log_to_firestore(f"💵 [{STRATEGY_KEY}] Prix OANDA : {entry}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur prix OANDA : {e}", level="ERROR")
        return

    # 🧲 Ajustement SL avec spread factor
    try:
        spread_factor = entry / candle["c"]
        sl_ref_oanda = sl_ref_polygon * spread_factor
    except ZeroDivisionError:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] candle['c'] == 0, division impossible.", level="ERROR")
        return

    # 📏 SL/TP
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref_oanda, direction)
    if risk_per_unit == 0:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul.", level="ERROR")
        return

    # 🖐️ Position
    units = compute_position_size(risk_per_unit, RISK_CHF)
    if units < 0.1:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Taille position trop faible ({units})", level="ERROR")
        return

    try:
        executed_units = execute_trade(entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"✅ [{STRATEGY_KEY}] Ordre {direction} exécuté ({executed_units})", level="TRADING")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur exécution : {e}", level="ERROR")
        return

    db.collection("trading_days").document(today).collection("trades").add({
        "strategy": STRATEGY_KEY,
        "entry": entry,
        "sl": sl_price,
        "tp": tp_price,
        "direction": direction,
        "units": executed_units,
        "timestamp": datetime.now().isoformat(),
        "source_candle_id": candle_id,
        "outcome": "unknown"
    })

    log_to_firestore(f"🚀 [{STRATEGY_KEY}] Trade exécuté à {entry} (SL: {sl_price}, TP: {tp_price})", level="TRADING")