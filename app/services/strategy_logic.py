#source: app/services/strategy_logic.py

from app.services.firebase import get_firestore
from app.services import oanda_service
from datetime import datetime
import pytz
from app.services.log_service import log_to_firestore
from math import floor
from datetime import timezone

logged_ranges = set()

def is_in_trading_window(ny_time):
    return datetime.strptime("09:45", "%H:%M").time() <= ny_time <= datetime.strptime("11:30", "%H:%M").time()

def is_strategy_active(db):
    doc = db.collection("config").document("strategies").get()
    return doc.exists and doc.to_dict().get("sp500_fake_breakout_active")

def get_opening_range(db, today):
    doc = db.collection("opening_range").document(today).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    return data if data.get("status") == "ready" else None

def has_trade_been_executed(db, today):
    doc = db.collection("trading_days").document(today).get()
    return doc.exists and doc.to_dict().get("executed")

def detect_fake_breakout(bar, high_15, low_15, range_size):
    if bar["h"] > high_15 and low_15 <= bar["c"] <= high_15:
        breakout = bar["h"] - high_15
        if breakout >= 0.15 * range_size:
            return "SHORT", breakout
    elif bar["l"] < low_15 and low_15 <= bar["c"] <= high_15:
        breakout = low_15 - bar["l"]
        if breakout >= 0.15 * range_size:
            return "LONG", breakout
    return None, None

def get_entry_price():
    return oanda_service.get_latest_price("SPX500_USD")

def calculate_sl_tp(entry, sl_level, direction):
    risk = abs(entry - sl_level)
    if risk == 0:
        return None, None, 0
    tp = entry + 1.75 * risk if direction == "LONG" else entry - 1.75 * risk
    return round(sl_level, 2), round(tp, 2), risk

def compute_position_size(risk_per_unit, risk_limit=50):
    if risk_per_unit == 0:
        return 0
    return round(risk_limit / risk_per_unit, 1)

def execute_trade(entry_price, sl_price, tp_price, units, direction):
    if direction == "SHORT":
        units = -units
    oanda_service.create_order(
        instrument="SPX500_USD",
        entry_price=entry_price,
        stop_loss_price=sl_price,
        take_profit_price=tp_price,
        units=units
    )
    return units

def save_trade_execution(db, today, entry, sl, tp, direction, units):
    db.collection("trading_days").document(today).set({
        "executed": True,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "direction": direction,
        "units": units,
        "timestamp": datetime.now().isoformat()
    })

def process_new_minute_bar(bar: dict):
    db = get_firestore()
    today = bar["day"]
    utc_dt = datetime.strptime(bar["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()


    if not is_in_trading_window(ny_time):
        print(f"⏱️ {bar['utc_time']} ignorée : hors fenêtre de trading (09:45–11:30 NY)")
        return

    if not is_strategy_active(db):
        log_to_firestore("❌ Stratégie SP500 désactivée dans Firestore.", level="INFO")
        return

    range_data = get_opening_range(db, today)
    if not range_data:
        log_to_firestore(f"📉 Range non prêt pour {today}.", level="RANGE")
        return

    high_15, low_15, range_size = range_data["high"], range_data["low"], range_data["range_size"]
    range_key = f"{today}-{high_15}-{low_15}"
    if range_key not in logged_ranges:
        log_to_firestore(f"📊 Opening Range {today} — High: {high_15}, Low: {low_15}, Size: {range_size:.2f}", level="RANGE")
        logged_ranges.add(range_key)

    if has_trade_been_executed(db, today):
        log_to_firestore(f"🔁 Trade déjà exécuté pour {today}.", level="TRADING")
        return

    direction, breakout = detect_fake_breakout(bar, high_15, low_15, range_size)
    if not direction:
        log_to_firestore("🔍 Aucune condition de breakout valide détectée.", level="NO_TRADING")
        return

    log_to_firestore(f"{'📈' if direction == 'LONG' else '📉'} Breakout {direction} détecté. Excès: {breakout:.2f}", level="TRADING")

    try:
        entry_price = get_entry_price()
        log_to_firestore(f"💵 Prix OANDA pour exécution : {entry_price}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ Erreur récupération prix OANDA : {e}", level="ERROR")
        return

    spread_factor = entry_price / bar["c"]
    sl_level = low_15 if direction == "LONG" else high_15
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry_price, sl_level * spread_factor, direction)

    if risk_per_unit == 0:
        log_to_firestore("❌ Risque par unité nul, impossible de trader.", level="ERROR")
        return

    units = compute_position_size(risk_per_unit)
    if units < 0.1:
        log_to_firestore(f"❌ Taille de position trop faible ({units}), ordre ignoré.", level="ERROR")
        return

    try:
        units_executed = execute_trade(entry_price, sl_price, tp_price, units, direction)
        log_to_firestore(f"✅ Ordre {direction} placé chez OANDA : {units_executed} unités", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ Erreur exécution ordre OANDA : {e}", level="ERROR")
        return

    save_trade_execution(db, today, entry_price, sl_price, tp_price, direction, units_executed)
    log_to_firestore(f"🚀 Signal {direction} exécuté à {entry_price} (SL: {sl_price}, TP: {tp_price})", level="TRADING")
