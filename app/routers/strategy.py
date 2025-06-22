from fastapi import APIRouter, Request
from app.services.firebase import get_firestore

router = APIRouter()

@router.get("/strategy/all")
def get_all_strategies():
    db = get_firestore()
    doc = db.collection("config").document("strategies").get()
    return doc.to_dict() or {}

@router.post("/strategy/toggle")
async def toggle_strategy(request: Request):
    body = await request.json()
    strategy_name = body.get("strategy")

    db = get_firestore()
    ref = db.collection("config").document("strategies")
    doc = ref.get()
    data = doc.to_dict() or {}

    current = data.get(strategy_name)
    if current is None:
        return {"error": "Stratégie inconnue dans Firestore."}

    ref.update({strategy_name: not current})
    return {strategy_name: not current}
