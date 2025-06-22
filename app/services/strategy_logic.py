from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.strategies import get_all_strategies
from datetime import datetime, timezone
import pytz

logged_ranges = set()

# 📆 Vérifie si on est dans la fenêtre de trading (NY)
def is_in_trading_window(ny_time):
    return datetime.strptime("09:45", "%H:%M").time() <= ny_time <= datetime.strptime("11:30", "%H:%M").time()

# 🔎 Récupère le range d’ouverture stocké en Firestore
def get_opening_range(db, today: str):
    doc = db.collection("opening_range").document(today).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    return data if data.get("status") == "ready" else None

# ✅ Vérifie si un trade a déjà été exécuté ce jour-là
def has_trade_been_executed(db, today: str):
    doc = db.collection("trading_days").document(today).get()
    return doc.exists and doc.to_dict().get("executed", False)

# 🧠 Fonction principale appelée à chaque nouvelle bougie 1m
def process_new_minute_bar(bar: dict):
    db = get_firestore()
    today = bar["day"]

    # Convertit UTC → NY
    utc_dt = datetime.strptime(bar["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()

    if not is_in_trading_window(ny_time):
        print(f"⏱️ {bar['utc_time']} ignorée : hors fenêtre de trading (09:45–11:30 NY)")
        return

    range_data = get_opening_range(db, today)
    if not range_data:
        log_to_firestore(f"📉 Range non prêt pour {today}.", level="RANGE")
        return

    # Log une seule fois par jour le range
    high_15 = range_data["high"]
    low_15 = range_data["low"]
    range_size = range_data["range_size"]
    range_key = f"{today}-{high_15}-{low_15}"

    if range_key not in logged_ranges:
        log_to_firestore(f"📊 Opening Range {today} — High: {high_15}, Low: {low_15}, Size: {range_size:.2f}", level="RANGE")
        logged_ranges.add(range_key)

    # 🔁 Applique chaque stratégie
    for strategy in get_all_strategies():
        try:
            strategy(bar, db, today, high_15, low_15, range_size)
        except Exception as e:
            log_to_firestore(f"❌ Erreur dans stratégie {strategy.__name__} → {e}", level="ERROR")
