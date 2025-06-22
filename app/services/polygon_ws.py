from datetime import datetime, timezone
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)

STRATEGY_KEY = "sp500_fake_breakout_strict"
RISK_CHF = 50

def process(candle):
    db = get_firestore()
    today = candle["day"]

    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()

    # 🕒 Ignore hors fenêtre de trading
    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    # ⚙️ Vérifie si activée
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # 📊 Récupère le range
    range_data = db.collection("opening_range").document(today).get().to_dict()
    if not range_data or range_data.get("status") != "ready":
        return

    # 🔁 Un seul trade par jour
    trade_doc = db.collection("trading_days").document(today).get()
    if trade_doc.exists and trade_doc.to_dict().get("executed", False):
        log_to_firestore(f"🔁 [{STRATEGY_KEY}] Trade déjà exécuté pour {today}", level="TRADING")
        return

    high_15 = range_data["high"]
    low_15 = range_data["low"]
    range_size = range_data["range_size"]

    # 🎯 Détection breakout strict
    direction, breakout = None, 0
    if candle["h"] > high_15 and low_15 <= candle["c"] <= high_15:
        breakout = candle["h"] - high_15
        if breakout >= 0.15 * range_size and candle["o"] >= low_15:
            direction = "SHORT"
    elif candle["l"] < low_15 and low_15 <= candle["c"] <= high_15:
        breakout = low_15 - candle["l"]
        if breakout >= 0.15 * range_size and candle["o"] <= high_15:
            direction = "LONG"

    if not direction:
        log_to_firestore(f"🔍 [{STRATEGY_KEY}] Aucun breakout valide détecté.", level="NO_TRADING")
        return

    log_to_firestore(f"[{STRATEGY_KEY}] {'📈' if direction == 'LONG' else '📉'} Signal {direction} détecté. Excès: {breakout:.2f}", level="TRADING")

    # 💵 Prix OANDA
    try:
        entry_price = get_entry_price()
        log_to_firestore(f"💵 [{STRATEGY_KEY}] Prix OANDA : {entry_price}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur récupération prix OANDA : {e}", level="ERROR")
        return

    # 📏 SL/TP
    spread_factor = entry_price / candle["c"]
    sl_ref = low_15 if direction == "LONG" else high_15
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry_price, sl_ref * spread_factor, direction)

    if risk_per_unit == 0:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul, trade ignoré", level="ERROR")
        return

    units = compute_position_size(risk_per_unit, RISK_CHF)
    if units < 0.1:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Taille de position trop faible ({units}), ignoré.", level="ERROR")
        return

    # ✅ Exécution ordre
    try:
        executed = execute_trade(entry_price, sl_price, tp_price, units, direction)
        db.collection("trading_days").document(today).set({
            "executed": True,
            "entry": entry_price,
            "sl": sl_price,
            "tp": tp_price,
            "direction": direction,
            "units": executed,
            "timestamp": datetime.now().isoformat()
        })
        log_to_firestore(f"✅ [{STRATEGY_KEY}] Ordre exécuté à {entry_price} (SL: {sl_price}, TP: {tp_price})", level="TRADING")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur exécution ordre : {e}", level="ERROR")
