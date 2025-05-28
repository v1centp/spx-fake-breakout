from fastapi import APIRouter, HTTPException
from app.services.firebase import get_firestore

router = APIRouter()

@router.get("/strategy/sp500/status")
def get_sp500_status():
    db = get_firestore()
    doc = db.collection("config").document("strategies").get()
    return {"active": doc.to_dict().get("sp500_fake_breakout_active", False)}

@router.post("/strategy/sp500/toggle")
def toggle_sp500_strategy():
    db = get_firestore()
    ref = db.collection("config").document("strategies")
    doc = ref.get()
    current = doc.to_dict().get("sp500_fake_breakout_active", False)
    ref.update({"sp500_fake_breakout_active": not current})
    return {"active": not current}
