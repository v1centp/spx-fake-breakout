from app.services.firebase import get_firestore
from app.services import oanda_service
from datetime import datetime
import pytz
from app.services.log_service import log_to_firestore
from math import floor

# Cache local pour éviter de logger plusieurs fois le même range
logged_ranges = set()

def process_new_minute_bar(bar: dict):
    db = get_firestore()
    today = bar["day"]
    ny_time = datetime.strptime(bar["utc_time"], "%Y-%m-%d %H:%M:%S").astimezone(
        pytz.timezone("America/New_York")
    ).time()

    if not (datetime.strptime("09:45", "%H:%M").time() <= ny_time <= datetime.strptime("11:30", "%H:%M").time()):
        print(f"⏱️ {bar['utc_time']} ignorée : hors fenêtre de trading (09:45–11:30 NY)")
        return

    strategy_doc = db.collection("config").document("strategies").get()
    if not strategy_doc.exists or not strategy_doc.to_dict().get("sp500_fake_breakout_active"):
        log_to_firestore("❌ Stratégie SP500 désactivée dans Firestore.", level="INFO")
        return

    range_doc = db.collection("opening_range").document(today).get()
    if not range_doc.exists or range_doc.to_dict().get("status") != "ready":
        log_to_firestore(f"📉 Range non prêt pour {today}.", level="RANGE")
        return

    range_data = range_doc.to_dict()
    high_15 = range_data["high"]
    low_15 = range_data["low"]
    range_size = range_data["range_size"]

    range_key = f"{today}-{high_15}-{low_15}"
    if range_key not in logged_ranges:
        log_to_firestore(f"📊 Opening Range {today} — High: {high_15}, Low: {low_15}, Size: {range_size:.2f}", level="RANGE")
        logged_ranges.add(range_key)

    trade_doc = db.collection("trading_days").document(today).get()
    if trade_doc.exists and trade_doc.to_dict().get("executed"):
        log_to_firestore(f"🔁 Trade déjà exécuté pour {today}.", level="TRADING")
        return

    direction = None
    if bar["h"] > high_15 and low_15 <= bar["c"] <= high_15:
        breakout = bar["h"] - high_15
        if breakout >= 0.15 * range_size:
            direction = "SHORT"
            log_to_firestore(f"📉 Breakout SHORT détecté. Excès: {breakout:.2f}", level="TRADING")
        else:
            log_to_firestore(f"↩️ Excès SHORT insuffisant ({breakout:.2f} < {0.15 * range_size:.2f})", level="TRADING")
    elif bar["l"] < low_15 and low_15 <= bar["c"] <= high_15:
        breakout = low_15 - bar["l"]
        if breakout >= 0.15 * range_size:
            direction = "LONG"
            log_to_firestore(f"📈 Breakout LONG détecté. Excès: {breakout:.2f}", level="TRADING")
        else:
            log_to_firestore(f"↩️ Excès LONG insuffisant ({breakout:.2f} < {0.15 * range_size:.2f})", level="TRADING")

    if not direction:
        log_to_firestore("🔍 Aucune condition de breakout valide détectée.", level="TRADING")
        return

    try:
        entry_price = oanda_service.get_latest_price("SPX500_USD")
        log_to_firestore(f"💵 Prix OANDA pour exécution : {entry_price}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ Erreur récupération prix OANDA : {e}", level="ERROR")
        return

    last_spx_close = bar["c"]
    spread_factor = entry_price / last_spx_close
    sl_spx = low_15 if direction == "LONG" else high_15
    stop_loss_price = round(sl_spx * spread_factor, 2)

    if direction == "LONG":
        take_profit_price = round(entry_price + 1.75 * (entry_price - stop_loss_price), 2)
    else:
        take_profit_price = round(entry_price - 1.75 * (stop_loss_price - entry_price), 2)

    risk_per_unit = abs(entry_price - stop_loss_price)
    if risk_per_unit == 0:
        log_to_firestore("❌ Risque par unité nul, impossible de trader.", level="ERROR")
        return

    units = round(50 / risk_per_unit, 1)
    if units < 0.1:
        log_to_firestore(f"❌ Taille de position trop faible ({units}), ordre ignoré.", level="ERROR")
        return

    if direction == "SHORT":
        units = -units

    try:
        oanda_service.create_order(
            instrument="SPX500_USD",
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            units=units
        )
        log_to_firestore(f"✅ Ordre {direction} placé chez OANDA : {units} unités", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ Erreur exécution ordre OANDA : {e}", level="ERROR")
        return

    db.collection("trading_days").document(today).set({
        "executed": True,
        "entry": entry_price,
        "sl": stop_loss_price,
        "tp": take_profit_price,
        "direction": direction,
        "units": units,
        "timestamp": datetime.now().isoformat()
    })
    log_to_firestore(f"🚀 Signal {direction} exécuté à {entry_price} (SL: {stop_loss_price}, TP: {take_profit_price})", level="TRADING")
