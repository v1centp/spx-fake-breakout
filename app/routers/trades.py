from fastapi import APIRouter
from app.services.firebase import get_firestore
from datetime import datetime

router = APIRouter()

@router.get("/trades")
def get_all_trades():
    db = get_firestore()
    trades_snap = db.collection_group("trades").stream()

    trades = []
    for doc in trades_snap:
        data = doc.to_dict()
        trade = {
            "id": doc.id,
            "strategy": data.get("strategy", "unknown"),
            "direction": data.get("direction"),
            "entry": data.get("entry"),
            "sl": data.get("sl"),
            "tp": data.get("tp"),
            "units": data.get("units"),
            "timestamp": data.get("timestamp"),
            "outcome": data.get("outcome", "unknown"),
            "justification": data.get("meta", {}).get("justification")
        }
        trades.append(trade)

    trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    return trades
