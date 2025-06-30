from datetime import datetime, timezone
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, execute_trade
)

STRATEGY_KEY = "spx_breakout_pullback_filtered"
RISK_CHF = 200
SENTIMENT_THRESHOLD_LONG = 70
SENTIMENT_THRESHOLD_SHORT = 30


def compute_position_size(entry, sl):
    risk_per_unit = abs(entry - sl)
    if risk_per_unit == 0:
        return 0
    return round(RISK_CHF / risk_per_unit, 1)


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

    high_15 = range_data["high"]
    low_15 = range_data["low"]
    range_size = range_data["range_size"]

    close = candle["c"]
    direction = None
    breakout = 0

    # Check confirmation de breakout enregistré
    temp_doc_ref = db.collection("temp_signals").document(today)
    temp_signal = temp_doc_ref.get()

    if temp_signal.exists:
        signal = temp_signal.to_dict()
        breakout_level = high_15 if signal["direction"] == "SHORT" else low_15
        retouch_range = 0.25 * range_size

        # Mesure d'un vrai retouch (contact du niveau clé)
        contact = (
            abs(candle["l"] - breakout_level) <= retouch_range if signal["direction"] == "LONG"
            else abs(candle["h"] - breakout_level) <= retouch_range
        )

        if contact:
            direction = signal["direction"]
            breakout = abs(signal["breakout"])
            temp_doc_ref.delete()
        else:
            temp_doc_ref.delete()
            return

    else:
        if candle["h"] > high_15:
            breakout = candle["h"] - high_15
            if breakout >= 0.15 * range_size:
                temp_doc_ref.set({
                    "direction": "SHORT",
                    "breakout": breakout,
                    "detected_at": candle["utc_time"]
                })
            return

        elif candle["l"] < low_15:
            breakout = low_15 - candle["l"]
            if breakout >= 0.15 * range_size:
                temp_doc_ref.set({
                    "direction": "LONG",
                    "breakout": breakout,
                    "detected_at": candle["utc_time"]
                })
            return
        else:
            return

    # 🔎 Vérifie la note des news fondamentales
    score_docs = db.collection("news_sentiment_score").order_by("timestamp", direction="DESCENDING").limit(1).stream()
    score_doc = next(score_docs, None)
    if score_doc:
        note = score_doc.to_dict().get("note", 50)
        if (direction == "LONG" and note < SENTIMENT_THRESHOLD_LONG) or (direction == "SHORT" and note > SENTIMENT_THRESHOLD_SHORT):
            log_to_firestore(f"🧠 [{STRATEGY_KEY}] Signal {direction} bloqué à cause du score news ({note})", level="NO_TRADING")
            return

    # Vérifie s'il y a déjà un trade dans la même direction qui n'était pas perdant
    trades_same_dir = list(db.collection("trading_days")
        .document(today)
        .collection("trades")
        .where("strategy", "==", STRATEGY_KEY)
        .where("direction", "==", direction)
        .stream())

    for t in trades_same_dir:
        outcome = t.to_dict().get("outcome")
        if outcome != "loss":
            log_to_firestore(f"🔁 [{STRATEGY_KEY}] Trade {direction} déjà pris avec outcome {outcome}", level="TRADING")
            return

    log_to_firestore(f"[{STRATEGY_KEY}] ✅ Signal confirmé {direction} après breakout de {breakout:.2f}", level="TRADING")

    try:
        entry = get_entry_price()
        log_to_firestore(f"💵 [{STRATEGY_KEY}] Prix OANDA : {entry}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur prix OANDA : {e}", level="ERROR")
        return

    buffer = max(0.3, 0.015 * range_size)
    spread_factor = entry / candle["c"]

    extreme_day = low_15 if direction == "LONG" else high_15
    sl_ref_polygon = min(extreme_day, candle["l"] - buffer) if direction == "LONG" else max(extreme_day, candle["h"] + buffer)
    sl_ref_oanda = sl_ref_polygon * spread_factor

    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref_oanda, direction)
    if risk_per_unit == 0:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul.", level="ERROR")
        return

    units = compute_position_size(entry, sl_price)
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
        
    })

    log_to_firestore(f"🚀 [{STRATEGY_KEY}] Trade confirmé exécuté à {entry} (SL: {sl_price}, TP: {tp_price})", level="TRADING")
