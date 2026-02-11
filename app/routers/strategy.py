from fastapi import APIRouter, Request
from app.services.firebase import get_firestore

router = APIRouter()

KNOWN_STRATEGIES = ["mean_revert", "trend_follow", "ichimoku", "news_trading"]

@router.get("/strategy/all")
def get_all_strategies():
    db = get_firestore()
    doc = db.collection("config").document("strategies").get()
    data = doc.to_dict() or {}
    return {name: data.get(name, False) for name in KNOWN_STRATEGIES}

@router.post("/strategy/toggle")
async def toggle_strategy(request: Request):
    body = await request.json()
    strategy_name = body.get("strategy")

    db = get_firestore()
    ref = db.collection("config").document("strategies")
    doc = ref.get()
    data = doc.to_dict() or {}

    current = data.get(strategy_name, False)
    ref.set({strategy_name: not current}, merge=True)
    return {strategy_name: not current}

@router.get("/config/risk")
def get_risk_config():
    db = get_firestore()
    doc = db.collection("config").document("settings").get()
    data = doc.to_dict() or {}
    return {
        "risk_chf": data.get("risk_chf", 50),
        "risk_usd_crypto": data.get("risk_usd_crypto", 50),
    }

@router.put("/config/risk")
async def update_risk_config(request: Request):
    body = await request.json()
    update = {}
    risk_chf = body.get("risk_chf")
    if risk_chf is not None:
        if not isinstance(risk_chf, (int, float)) or risk_chf <= 0:
            return {"error": "risk_chf doit etre un nombre positif"}
        update["risk_chf"] = risk_chf
    risk_usd_crypto = body.get("risk_usd_crypto")
    if risk_usd_crypto is not None:
        if not isinstance(risk_usd_crypto, (int, float)) or risk_usd_crypto <= 0:
            return {"error": "risk_usd_crypto doit etre un nombre positif"}
        update["risk_usd_crypto"] = risk_usd_crypto
    if not update:
        return {"error": "Aucune valeur fournie"}
    db = get_firestore()
    db.collection("config").document("settings").set(update, merge=True)
    return update
