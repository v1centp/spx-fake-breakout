from fastapi import APIRouter
from app.services.firebase import get_firestore
from datetime import datetime

router = APIRouter()

@router.get("/trades")
def get_all_trades():
    db = get_firestore()

    # 1. Tous les trades classiques
    classic_trades_snap = db.collection_group("trades").stream()
    classic_trades = [doc.to_dict() | {"id": doc.id} for doc in classic_trades_snap]

    # 2. Tous les trades GPT (sous-collections `executions`)
    executions_snap = db.collection_group("executions").stream()
    executions = []
    for doc in executions_snap:
        data = doc.to_dict()
        strategy = data.get("strategy") or data.get("meta", {}).get("strategy") or "gpt_trader"
        executions.append({
            "id": doc.id,
            "strategy": strategy,
            "direction": data.get("direction"),
            "entry": data.get("entry"),
            "sl": data.get("sl"),
            "tp": data.get("tp"),
            "units": data.get("units"),
            "timestamp": data.get("timestamp"),
            "outcome": data.get("outcome", "unknown"),
            "meta": data.get("meta", {})
        })

    all_trades = classic_trades + executions
    all_trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    return all_trades
