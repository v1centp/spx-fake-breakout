from fastapi import APIRouter
from pydantic import BaseModel
from app.services.firebase import get_firestore
from datetime import datetime, timezone
from typing import List

router = APIRouter()

# ✅ Endpoint pour consulter les dernières bougies stockées
@router.get("/candles", response_model=List[dict])
async def get_candles(day: str):
    db = get_firestore()
    query = db.collection("ohlc_1m").where("day", "==", day).order_by("s")
    docs = query.stream()
    return [doc.to_dict() for doc in docs]


# ✅ Endpoint pour récupérer le range d'ouverture d'un jour donné
@router.get("/opening_range/{day}")
def get_opening_range(day: str):
    db = get_firestore()
    doc = db.collection("opening_range").document(day).get()
    if doc.exists:
        return doc.to_dict()
    return {"message": "Not found"}, 404
