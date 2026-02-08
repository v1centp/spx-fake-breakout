from fastapi import APIRouter
from google.cloud.firestore_v1 import FieldFilter
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


@router.get("/trades/{oanda_trade_id}/events")
def get_trade_events(oanda_trade_id: str):
    db = get_firestore()

    # Find the trade doc by oanda_trade_id
    docs = db.collection_group("trades").where(
        filter=FieldFilter("oanda_trade_id", "==", oanda_trade_id)
    ).stream()

    trade_doc = None
    for doc in docs:
        if doc.to_dict().get("entry"):
            trade_doc = doc
            break

    if not trade_doc:
        return []

    # Get events subcollection ordered by timestamp
    events = trade_doc.reference.collection("events").order_by("timestamp").stream()
    return [e.to_dict() for e in events]
