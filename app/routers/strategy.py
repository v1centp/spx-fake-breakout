from fastapi import APIRouter, HTTPException, Request
from app.services.firebase import get_firestore

router = APIRouter()

@router.get("/strategies")
def get_all_strategies():
    db = get_firestore()
    doc = db.collection("config").document("strategies").get()
    return doc.to_dict() or {}

@router.post("/strategies/toggle")
async def toggle_strategy(request: Request):
    body = await request.json()
    strategy_key = body.get("strategy")

    if not strategy_key:
        raise HTTPException(status_code=400, detail="Clé stratégie manquante")

    db = get_firestore()
    ref = db.collection("config").document("strategies")
    doc = ref.get()
    data = doc.to_dict() or {}

    if strategy_key not in data:
        raise HTTPException(status_code=404, detail="Stratégie inconnue dans Firestore")

    new_state = not data[strategy_key]
    ref.update({strategy_key: new_state})

    return {strategy_key: new_state}
