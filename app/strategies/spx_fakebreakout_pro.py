from datetime import datetime, timezone
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)

STRATEGY_KEY = "spx_fakebreakout_pro"
RISK_CHF = 200

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
        log_to_firestore(f"🕒 [{STRATEGY_KEY}] Bougie hors plage horaire", level="NO_TRADING")
        return

    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        log_to_firestore(f"⚙️ [{STRATEGY_KEY}] Stratégie désactivée", level="NO_TRADING")
        return

    range_data = db.collection("opening_range").document(today).get().to_dict()
    if not range_data or range_data.get("status") != "ready":
        log_to_firestore(f"📊 [{STRATEGY_KEY}] Range d'ouverture non prêt", level="NO_TRADING")
        return

    high_15 = range_data["high"]
    low_15 = range_data["low"]
    range_size = range_data["range_size"]

    close = candle["c"]
    direction = None
    breakout = 0
    candle_id = f"SPX_{candle['e']}"

    # ✅ Détection fake breakout
    if candle["h"] > high_15 and low_15 <= close <= high_15:
        excess = candle["h"] - high_15
        if excess >= 0.15 * range_size:
            direction = "SHORT"
            breakout = excess
            log_to_firestore(f"🎯 [{STRATEGY_KEY}] Excès haussier détecté ({excess:.2f}) → retour dans le range", level="TRADING")
        else:
            log_to_firestore(f"❌ [{STRATEGY_KEY}] Excès haussier insuffisant ({excess:.2f})", level="NO_TRADING")

    elif candle["l"] < low_15 and low_15 <= close <= high_15:
        excess = low_15 - candle["l"]
        if excess >= 0.15 * range_size:
            direction = "LONG"
            breakout = excess
            log_to_firestore(f"🎯 [{STRATEGY_KEY}] Excès baissier détecté ({excess:.2f}) → retour dans le range", level="TRADING")
        else:
            log_to_firestore(f"❌ [{STRATEGY_KEY}] Excès baissier insuffisant ({excess:.2f})", level="NO_TRADING")
    else:
        log_to_firestore(f"🔍 [{STRATEGY_KEY}] Aucun fake breakout détecté sur cette bougie", level="NO_TRADING")

    if not direction:
        db.collection("ohlc_1m").document(candle_id).update({f"strategy_decisions.{STRATEGY_KEY}": "REJECT: aucun signal"})
        return

    db.collection("ohlc_1m").document(candle_id).update({f"strategy_decisions.{STRATEGY_KEY}": f"ACCEPT: signal {direction}"})

    # 🔎 Vérifie le score des news fondamentales
    score_docs = db.collection("news_sentiment_score").order_by("timestamp", direction="DESCENDING").limit(1).stream()
    score_doc = next(score_docs, None)
    if score_doc:
        note = score_doc.to_dict().get("note", 50)
        if (direction == "LONG" and note < 30) or (direction == "SHORT" and note > 70):
            log_to_firestore(f"🧐 [{STRATEGY_KEY}] Signal {direction} bloqué par le score news ({note})", level="NO_TRADING")
            return

    trades_same_dir = list(db.collection("trading_days")
        .document(today)
        .collection("trades")
        .where("strategy", "==", STRATEGY_KEY)
        .where("direction", "==", direction)
        .stream())

    if trades_same_dir:
        log_to_firestore(f"🔁 [{STRATEGY_KEY}] Trade {direction} déjà exécuté aujourd'hui", level="NO_TRADING")
        return

    try:
        entry = get_entry_price()
        log_to_firestore(f"💵 [{STRATEGY_KEY}] Prix OANDA reçu : {entry}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur récupération prix OANDA : {e}", level="ERROR")
        return

    buffer = max(0.3, 0.015 * range_size)
    spread_factor = entry / candle["c"]

    sl_ref_polygon = candle["h"] + buffer if direction == "SHORT" else candle["l"] - buffer
    sl_ref_oanda = sl_ref_polygon * spread_factor

    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref_oanda, direction)
    if risk_per_unit == 0:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul détecté", level="ERROR")
        return

    units = compute_position_size(entry, sl_price)
    if units < 0.1:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Taille de position trop faible ({units})", level="ERROR")
        return

    try:
        executed_units = execute_trade(entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"✅ [{STRATEGY_KEY}] Trade {direction} exécuté ({executed_units} unités)", level="TRADING")
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

    log_to_firestore(f"🚀 [{STRATEGY_KEY}] Fake breakout exécuté à {entry} (SL: {sl_price}, TP: {tp_price})", level="TRADING")