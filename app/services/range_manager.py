from app.services.firebase import get_firestore
from datetime import datetime, time
import pytz
from app.services.log_service import log_to_firestore

def calculate_and_store_opening_range(day_str: str):
    db = get_firestore()

    docs = db.collection("ohlc_1m") \
        .where("day", "==", day_str) \
        .where("in_opening_range", "==", True) \
        .where("sym", "==", "I:SPX") \
        .stream()

    candles = [doc.to_dict() for doc in docs]
    if len(candles) < 15:
        return False

    high_15 = max(c["h"] for c in candles)
    low_15 = min(c["l"] for c in candles)
    range_size = high_15 - low_15

    db.collection("opening_range").document(day_str).set({
        "day": day_str,
        "high": high_15,
        "low": low_15,
        "range_size": range_size,
        "status": "ready"
    })
    log_to_firestore(f"📊 Opening Range {day_str} — High: {high_15}, Low: {low_15}, Size: {range_size:.2f}")
    return True
