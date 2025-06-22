from datetime import datetime, timezone
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)

STRATEGY_KEY = "sp500_fake_breakout_soft"
RISK_CHF = 50

def process(candle):
    db = get_firestore()
    today = candle["day"]

    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()

    # 🕒 Fenêtre de trading
    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    # ⚙️ Vérifie si la stratégie est activée
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # 📊 Range d’ouverture
    range_data = db.collection("opening_range").document(today).get().to_dict()
    if not range_data or range_data.get("status") != "ready":
        return

    # 🔁 Vérifie exécution précédente
    trade_doc = db.collection("trading_days").document(today).collection("trades").document(STRATEGY_KEY).get()
    if trade_doc.exists:
        log_to_firestore(f"🔁 [{STRATEGY_KEY}] Déjà exécutée aujourd'hui.", level="TRADING")
        return

    high_15 = range_data["high"]
    low_15 = range_data["low"]
    range_size = range_data["range_size"]

    # 🎯 Détection breakout soft
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
        log_to_firestore(f"🔍 [{STRATEGY_KEY}] Aucun breakout valide détecté.", level="NO_TRADING")
        return

    log_to_firestore(f"[{STRATEGY_KEY}] {'📈' if direction == 'LONG' else '📉'} Signal {direction} détecté. Excès: {breakout:.2f}", level="TRADING")

    # 💵 Récupération prix OANDA
    try:
        entry = get_entry_price()
        log_to_firestore(f"💵 [{STRATEGY_KEY}] Prix OANDA : {entry}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur récupération prix OANDA : {e}", level="ERROR")
        return

    # 📏 SL / TP
    spread_factor = entry / candle["c"]
    sl_ref = low_15 if direction == "LONG" else high_15
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref * spread_factor, direction)

    if risk_per_unit == 0:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul, trade ignoré", level="ERROR")
        return

    units = compute_position_size(risk_per_unit, RISK_CHF)
    if units < 0.1:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Taille position trop faible ({units}), ignoré.", level="ERROR")
        return

    # ✅ Exécution ordre
    try:
        executed_units = execute_trade(entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"✅ [{STRATEGY_KEY}] Ordre {direction} exécuté ({executed_units} unités)", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur exécution ordre : {e}", level="ERROR")
        return

    # 📝 Enregistrement
    db.collection("trading_days").document(today).collection("trades").document(STRATEGY_KEY).set({
        "entry": entry,
        "sl": sl_price,
        "tp": tp_price,
        "direction": direction,
        "units": executed_units,
        "timestamp": datetime.now().isoformat()
    })

    log_to_firestore(f"🚀 [{STRATEGY_KEY}] Trade exécuté à {entry} (SL: {sl_price}, TP: {tp_price})", level="TRADING")
