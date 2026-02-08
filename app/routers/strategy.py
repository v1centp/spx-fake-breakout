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

@router.get("/config/risk")
def get_risk_config():
    db = get_firestore()
    doc = db.collection("config").document("settings").get()
    data = doc.to_dict() or {}
    return {"risk_chf": data.get("risk_chf", 50)}

@router.put("/config/risk")
async def update_risk_config(request: Request):
    body = await request.json()
    risk_chf = body.get("risk_chf")
    if risk_chf is None or not isinstance(risk_chf, (int, float)) or risk_chf <= 0:
        return {"error": "risk_chf doit etre un nombre positif"}
    db = get_firestore()
    db.collection("config").document("settings").set({"risk_chf": risk_chf}, merge=True)
    return {"risk_chf": risk_chf}
