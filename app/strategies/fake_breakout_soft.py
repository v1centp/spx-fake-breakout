from datetime import datetime, timezone
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)

STRATEGY_KEY = "sp500_fake_breakout_soft"
RISK_CHF = 20

def process(candle):
    db = get_firestore()
    today = candle["day"]

    # 🕒 Conversion heure NY
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()

    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    # ⚙️ Vérifie activation
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # 📊 Range d'ouverture
    range_data = db.collection("opening_range").document(today).get().to_dict()
    if not range_data or range_data.get("status") != "ready":
        return

    high_15 = range_data["high"]
    low_15 = range_data["low"]
    range_size = range_data["range_size"]

    decision_msg = ""
    direction, breakout = None, None
    message = None
    close = candle["c"]

    if low_15 <= candle["o"] <= high_15:
        decision_msg = "Open dans le range → strat soft ignorée"
        log_to_firestore(f"🚫 [{STRATEGY_KEY}] {decision_msg}", level="NO_TRADING")
    elif candle["h"] > high_15:
        breakout = candle["h"] - high_15
        if breakout < 0.15 * range_size:
            decision_msg = f"Breakout haussier insuffisant ({breakout:.2f})"
            log_to_firestore(f"🔍 [{STRATEGY_KEY}] {decision_msg}", level="NO_TRADING")
        elif not (low_15 <= close <= high_15):
            decision_msg = f"Breakout haussier mais close hors range ({close})"
            log_to_firestore(f"🔍 [{STRATEGY_KEY}] {decision_msg}", level="NO_TRADING")
        else:
            direction = "SHORT"
            decision_msg = f"Signal SHORT détecté. Excès: {breakout:.2f}"
    elif candle["l"] < low_15:
        breakout = low_15 - candle["l"]
        if breakout < 0.15 * range_size:
            decision_msg = f"Breakout baissier insuffisant ({breakout:.2f})"
            log_to_firestore(f"🔍 [{STRATEGY_KEY}] {decision_msg}", level="NO_TRADING")
        elif not (low_15 <= close <= high_15):
            decision_msg = f"Breakout baissier mais close hors range ({close})"
            log_to_firestore(f"🔍 [{STRATEGY_KEY}] {decision_msg}", level="NO_TRADING")
        else:
            direction = "LONG"
            decision_msg = f"Signal LONG détecté. Excès: {breakout:.2f}"
    else:
        decision_msg = f"Aucun breakout valide."
        log_to_firestore(f"🔍 [{STRATEGY_KEY}] {decision_msg}", level="NO_TRADING")

    candle_id = f"{candle['sym']}_{candle['e']}"
    db.collection("ohlc_1m").document(candle_id).update({f"strategy_decisions.{STRATEGY_KEY}": decision_msg})

    if not direction:
        return

    trades_same_dir = list(db.collection("trading_days")
        .document(today)
        .collection("trades")
        .where("strategy", "==", STRATEGY_KEY)
        .where("direction", "==", direction)
        .stream())

    if trades_same_dir:
        log_to_firestore(f"🔁 [{STRATEGY_KEY}] Trade {direction} déjà exécuté aujourd'hui.", level="TRADING")
        return

    log_to_firestore(f"[{STRATEGY_KEY}] {'📈' if direction == 'LONG' else '📉'} {decision_msg}", level="TRADING")

    try:
        entry = get_entry_price()
        log_to_firestore(f"💵 [{STRATEGY_KEY}] Prix OANDA : {entry}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur prix OANDA : {e}", level="ERROR")
        return

    buffer = max(0.3, 0.015 * range_size)
    spread_factor = entry / candle["c"]
    sl_ref_polygon = (candle["l"] - buffer) if direction == "LONG" else (candle["h"] + buffer)
    sl_ref_oanda = sl_ref_polygon * spread_factor

    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref_oanda, direction)
    if risk_per_unit == 0:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul.", level="ERROR")
        return

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
