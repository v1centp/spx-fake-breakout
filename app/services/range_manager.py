# app/services/range_manager.py
from app.utils.symbols import normalize_symbol
from app.services.firebase import get_firestore

def calculate_and_store_opening_range(day: str, symbol: str):
    db = get_firestore()
    sym = normalize_symbol(symbol)

    q = (db.collection("ohlc_1m")
           .where("day", "==", day)
           .where("sym", "==", sym)
           .where("in_opening_range", "==", True))

    highs, lows = [], []
    for d in q.stream():
        x = d.to_dict() or {}
        if "h" in x and "l" in x:
            highs.append(float(x["h"]))
            lows.append(float(x["l"]))

    doc_id = f"{day}_{sym}"
    if not highs or not lows:
        db.collection("opening_range").document(doc_id).set({"day": day, "symbol": sym, "status": "empty"})
        return

    hi, lo = max(highs), min(lows)
    db.collection("opening_range").document(doc_id).set({
        "day": day, "symbol": sym, "high": hi, "low": lo, "range": hi - lo, "status": "ready"
    })
