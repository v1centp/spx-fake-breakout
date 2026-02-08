from fastapi import APIRouter
from app.services.firebase import get_firestore

router = APIRouter()

@router.get("/trades")
def get_all_trades():
    db = get_firestore()

    trades_snap = db.collection_group("trades").stream()
    trades = []
    for doc in trades_snap:
        data = doc.to_dict()
        if not data.get("entry"):
            continue
        trades.append(data | {"id": doc.id})

    trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    return trades
