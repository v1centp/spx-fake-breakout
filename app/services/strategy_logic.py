from app.services.firebase import get_firestore
from app.services import oanda_service
from datetime import datetime
import pytz

def process_new_minute_bar(bar: dict):
    db = get_firestore()
    today = bar["day"]
    ny_time = datetime.strptime(bar["utc_time"], "%Y-%m-%d %H:%M:%S").astimezone(pytz.timezone("America/New_York")).time()

    if not (datetime.strptime("09:45", "%H:%M").time() <= ny_time <= datetime.strptime("11:30", "%H:%M").time()):
        return

    # Vérifier si stratégie active
    strategy_doc = db.collection("config").document("strategies").get()
    if not strategy_doc.exists or not strategy_doc.to_dict().get("sp500_fake_breakout_active"):
        return

    # Vérifier le range
    range_doc = db.collection("opening_range").document(today).get()
    if not range_doc.exists or range_doc.to_dict().get("status") != "ready":
        return

    range_data = range_doc.to_dict()
    high_15, low_15, range_size = range_data["high"], range_data["low"], range_data["range_size"]

    # Ne pas trader plusieurs fois
    trade_doc = db.collection("trading_days").document(today).get()
    if trade_doc.exists and trade_doc.to_dict().get("executed"):
        return

    direction = None
    if bar["high"] > high_15 and low_15 <= bar["c"] <= high_15:
        if (bar["high"] - high_15) >= 0.15 * range_size:
            direction = "SHORT"
    elif bar["low"] < low_15 and low_15 <= bar["c"] <= high_15:
        if (low_15 - bar["low"]) >= 0.15 * range_size:
            direction = "LONG"

    if not direction:
        return

    # Exécution via OANDA
    instrument = "US500USD"  # CFD correspondant
    entry_price = bar["c"]
    sl = entry_price + 10 if direction == "SHORT" else entry_price - 10
    tp = entry_price - 17.5 if direction == "SHORT" else entry_price + 17.5
    units = -10 if direction == "SHORT" else 10  # Choisir ton levier et volume

    try:
        oanda_service.create_order(instrument, units)
    except Exception as e:
        print(f"⚠️ Erreur exécution ordre OANDA : {e}")
        return

    db.collection("trading_days").document(today).set({
        "executed": True,
        "entry": entry_price,
        "sl": sl,
        "tp": tp,
        "direction": direction,
        "timestamp": datetime.utcnow().isoformat()
    })
    print(f"🚀 Signal {direction} exécuté à {entry_price}")
