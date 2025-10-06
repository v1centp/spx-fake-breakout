# app/services/range_manager.py
from app.services.firebase import get_firestore

def calculate_and_store_opening_range(day: str, symbol: str):
    """
    Calcule le range d'ouverture (high/low) pour un symbole et une journée donnée,
    à partir des bougies 1 minute stockées dans Firestore.
    """
    db = get_firestore()

    # même symbole exact que celui stocké dans ohlc_1m
    q = (
        db.collection("ohlc_1m")
        .where("day", "==", day)
        .where("sym", "==", symbol)
        .where("in_opening_range", "==", True)
    )

    highs, lows = [], []
    for doc in q.stream():
        data = doc.to_dict() or {}
        if "h" in data and "l" in data:
            try:
                highs.append(float(data["h"]))
                lows.append(float(data["l"]))
            except Exception:
                continue

    doc_id = f"{day}_{symbol}"

    if not highs or not lows:
        db.collection("opening_range").document(doc_id).set({
            "day": day,
            "symbol": symbol,
            "status": "empty"
        })
        return

    hi, lo = max(highs), min(lows)
    db.collection("opening_range").document(doc_id).set({
        "day": day,
        "symbol": symbol,
        "high": hi,
        "low": lo,
        "range": hi - lo,
        "status": "ready"
    })
