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

    # 🕒 Heure New York
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()

    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    # ⚙️ Activation stratégie
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # 📊 Range ouverture
    range_data = db.collection("opening_range").document(today).get().to_dict()
    if not range_data or range_data.get("status") != "ready":
        return

    high_15 = range_data["high"]
    low_15 = range_data["low"]
    range_size = range_data["range_size"]

    # 🎯 Breakout strict
    direction, breakout = None, None
    message = None
    close = candle["c"]

    if candle["h"] > high_15:
        breakout = candle["h"] - high_15
        if breakout < 0.15 * range_size:
            message = f"🔍 [{STRATEGY_KEY}] Breakout haussier détecté mais amplitude insuffisante ({breakout:.2f} < {0.15 * range_size:.2f})"
        elif not (low_15 <= close <= high_15):
            message = f"🔍 [{STRATEGY_KEY}] Breakout haussier détecté mais close hors range ({close})"
        elif not (low_15 <= candle["o"] <= high_15):
            message = f"🔍 [{STRATEGY_KEY}] Breakout haussier détecté mais open hors range ({candle['o']})"

        else:
            direction = "SHORT"

    elif candle["l"] < low_15:
        breakout = low_15 - candle["l"]
        if breakout < 0.15 * range_size:
            message = f"🔍 [{STRATEGY_KEY}] Breakout baissier détecté mais amplitude insuffisante ({breakout:.2f} < {0.15 * range_size:.2f})"
        elif not (low_15 <= close <= high_15):
            message = f"🔍 [{STRATEGY_KEY}] Breakout baissier détecté mais close hors range ({close})"
        elif not (low_15 <= candle["o"] <= high_15):
            message = f"🔍 [{STRATEGY_KEY}] Breakout baissier détecté mais open hors range ({candle['o']})"

        else:
            direction = "LONG"

    if not direction:
        log_to_firestore(message or f"🔍 [{STRATEGY_KEY}] Aucun breakout valide détecté.", level="NO_TRADING")
        return

    log_to_firestore(f"[{STRATEGY_KEY}] {'📈' if direction == 'LONG' else '📉'} Signal {direction} détecté. Excès: {breakout:.2f}", level="TRADING")

    # 💵 Prix OANDA
    try:
        entry = get_entry_price()
        log_to_firestore(f"💵 [{STRATEGY_KEY}] Prix OANDA : {entry}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur récupération prix OANDA : {e}", level="ERROR")
        return

    # 🛡️ Buffer SL
    buffer = max(0.3, 0.015 * range_size)
    spread_factor = entry / candle["c"]  # candle["c"] = close Polygon
    sl_ref_polygon = (candle["l"] - buffer) if direction == "LONG" else (candle["h"] + buffer)
    sl_ref_oanda = sl_ref_polygon * spread_factor

    # 📏 SL / TP
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref_oanda, direction)
    if risk_per_unit == 0:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul, ignoré.", level="ERROR")
        return

    # 🧮 Taille position
    units = compute_position_size(risk_per_unit, RISK_CHF)
    if units < 0.1:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Taille position trop faible ({units}), ignoré.", level="ERROR")
        return

    # 🔁 Déjà exécuté ?
    trade_doc = db.collection("trading_days").document(today).collection("trades").document(STRATEGY_KEY).get()
    if trade_doc.exists:
        log_to_firestore(f"🔁 [{STRATEGY_KEY}] Déjà exécutée aujourd'hui.", level="TRADING")
        return

    # ✅ Exécution
    try:
        executed_units = execute_trade(entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"✅ [{STRATEGY_KEY}] Ordre exécuté : {executed_units} unités", level="TRADING")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur exécution ordre : {e}", level="ERROR")
        return

    # 📝 Sauvegarde
    db.collection("trading_days").document(today).collection("trades").document(STRATEGY_KEY).set({
        "entry": entry,
        "sl": sl_price,
        "tp": tp_price,
        "direction": direction,
        "units": executed_units,
        "timestamp": datetime.now().isoformat()
    })

    log_to_firestore(f"🚀 [{STRATEGY_KEY}] Trade exécuté à {entry} (SL: {sl_price}, TP: {tp_price})", level="TRADING")
