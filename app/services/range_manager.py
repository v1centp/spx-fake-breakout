# app/services/range_manager.py
from app.services.firebase import get_firestore

def calculate_and_store_opening_range(day: str, symbol: str):
    db = get_firestore()

    q = (db.collection("ohlc_1m")
           .where("day", "==", day)
           .where("sym", "==", symbol)
           .where("in_opening_range", "==", True))

    highs, lows = [], []
    for d in q.stream():
        x = d.to_dict() or {}
        try:
            h = float(x["h"]); l = float(x["l"])
        except (KeyError, TypeError, ValueError):
            continue
        highs.append(h); lows.append(l)

    doc_ref = db.collection("opening_range").document(f"{day}_{symbol}")

    if not highs or not lows:
        doc_ref.set({"day": day, "symbol": symbol, "status": "empty", "count": 0})
        return

    hi, lo = max(highs), min(lows)
    doc_ref.set({
        "day": day,
        "symbol": symbol,
        "high": hi,
        "low": lo,
        "range": round(hi - lo, 5),
        "count": len(highs),
        "status": "ready"
    })
